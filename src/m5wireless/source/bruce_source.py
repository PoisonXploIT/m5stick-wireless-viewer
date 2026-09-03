"""Fuente de datos: Bruce por CLI serial (M5Stick, storage en LittleFS o SD).

Bruce no emite un stream continuo: es una CLI tipo Flipper a 115200. Esta
fuente hace dos cosas a la vez sobre el mismo puerto:

1. **Lineas de consola** -> al callback de linea (mismo contrato que
   :class:`SerialSource`), para que ``BruceConsoleParser`` vea el ciclo de
   vida (`Selected: ...`, `Sniffer started!`, errores SD).
2. **Poller de storage**: cada ``poll_interval`` segundos envia
   ``storage list <dir>`` a cada directorio configurado, compara tamanos con
   la ultima lista y, para ficheros nuevos o cambiados, envia
   ``storage read <path>`` y entrega los bytes crudos al callback de
   ficheros (``observe_files``).

Protocolo validado en hardware (ver SEGUIMIENTO.md):

- ``storage list <dir>`` responde con lineas ``<nombre> <tamano>`` hasta que
  la consola vuelve a estar quieta (fin de respuesta = silencio serial).
- ``storage read <path>`` responde con un echo ``COMMAND: ...\\r\\n`` de
  longitud variable y luego los bytes crudos. El lector salta el header
  buscando el magic del pcap (``d4c3b2a1``) y lee exactamente ``tamano``
  bytes (el tamano sale de la lista, asi que no depende del echo).

El transporte serial es inyectable (``_open_port``) para probar sin hardware.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import AbstractSource, LineCallback

logger = logging.getLogger(__name__)

PCAP_MAGIC = b"\xd4\xc3\xb2\xa1"

# Entrada de lista: `<nombre> <tamano>` (el nombre no lleva espacios).
# El listado real de Bruce usa TAB como separador y muestra directorios
# como ``<nombre>\t<DIR>`` (la regex los descarta por no ser numerico).
_LISTING_ENTRY_RE = re.compile(r"^(\S+)\s+(\d+)\s*$")
# Rondas de lectura vacia seguidas antes de abortar una `storage read`:
# si la ruta no existe el dispositivo solo devuelve el echo y el prompt,
# sin bytes, y el bucle colgaria para siempre sin esta guarda.
_IDLE_ABORT_ROUNDS = 3


class BruceStorageSource(AbstractSource):
    """CLI serial de Bruce: consola en vivo + poller de ``storage list/read``."""

    def __init__(
        self,
        port: str | None = None,
        baudrate: int = 115200,
        *,
        dirs: tuple[str, ...] = ("BrucePCAP/handshakes", "BrucePCAP"),
        poll_interval: float = 5.0,
        file_extension: str = ".pcap",
        max_retries: int = 5,
        base_backoff: float = 1.0,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._dirs = dirs
        self._poll_interval = poll_interval
        self._file_extension = file_extension
        self._max_retries = max_retries
        self._base_backoff = base_backoff
        self._running = False
        self._state = "esperando"
        self._seen: dict[str, int] = {}  # ruta -> tamano del ultimo read OK
        self._file_callbacks: list[Callable[[str, bytes], None]] = []
        self._callbacks_lock = threading.Lock()
        self._last_poll: float | None = None

    # ---- API publica ----
    def status(self) -> dict[str, object]:
        return {
            "state": self._state,
            "port": self._port,
            "baudrate": self._baudrate,
            "dirs": list(self._dirs),
            "files_read": len(self._seen),
        }

    def observe_files(self, callback: Callable[[str, bytes], None]) -> None:
        """Registra un callback de ficheros extraidos ``(ruta, bytes)``.

        Se invoca desde el hilo del poller: debe ser thread-safe y rapido
        (p. ej. parsear a eventos y/o guardar artifact).
        """
        with self._callbacks_lock:
            self._file_callbacks.append(callback)

    async def start(self, callback: LineCallback) -> None:
        loop = asyncio.get_running_loop()
        self._running = True
        attempt = 0
        while self._running:
            try:
                await loop.run_in_executor(None, self._worker, callback)
                break  # worker terminado por stop().
            except Exception:
                self._state = "reconectando"
                logger.exception("error en la fuente Bruce; se reintentara")
            attempt += 1
            if not self._running or attempt > self._max_retries:
                if attempt > self._max_retries:
                    logger.error("agotados los %d reintentos de Bruce", self._max_retries)
                break
            delay = min(self._base_backoff * (2 ** (attempt - 1)), 30.0)
            await asyncio.sleep(delay)

    async def stop(self) -> None:
        self._running = False

    # ---- worker (hilo dedicado, bloqueante) ----
    def _worker(self, line_callback: LineCallback) -> None:
        handle: Any = None
        try:
            handle = self._open_port()
            self._state = "conectado"
            next_poll = 0.0
            while self._running:
                raw = handle.readline()  # timeout=1: b'' si esta quieto.
                if raw:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line:
                        line_callback(line)
                now = time.monotonic()
                if self._running and now >= next_poll:
                    next_poll = now + self._poll_interval
                    for directory in self._dirs:
                        if not self._running:
                            break
                        self._poll_directory(handle, directory)
        finally:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    logger.exception("no se pudo cerrar el puerto serial de Bruce")

    def _open_port(self) -> Any:
        """Abre el puerto real. Inyectable en tests con un transporte falso."""
        try:
            import serial  # dependencia opcional; import perezoso.
        except ImportError as exc:
            raise RuntimeError("pyserial no esta instalado; instala el extra [serial]") from exc
        port = self._port if self._port is not None else _autodetect_bruce()
        return serial.Serial(port=port, baudrate=self._baudrate, timeout=1)

    # ---- poller de storage ----
    def _poll_directory(self, handle: Any, directory: str) -> None:
        self._state = "listando"
        entries = self._read_listing(handle, directory)
        for name, size in entries:
            if not self._running:
                break
            if not name.endswith(self._file_extension):
                continue
            # El listado trae nombres relativos al directorio; `storage read`
            # exige la ruta completa (validado contra hardware real).
            path = name if "/" in name else f"{directory}/{name}"
            last_size = self._seen.get(path)
            if last_size == size:
                continue  # ya lo leimos con este tamano.
            data = self._read_file(handle, path, size)
            if data is None:
                continue
            self._seen[path] = len(data)
            self._emit_file(path, data)
        if self._running:
            self._state = "conectado"

    def _read_listing(self, handle: Any, directory: str) -> list[tuple[str, int]]:
        """Envia ``storage list <dir>`` y recoge lineas hasta el silencio."""
        handle.write(f"storage list {directory}\r\n".encode())
        entries: list[tuple[str, int]] = []
        idle_rounds = 0
        while self._running and idle_rounds < 2:
            raw = handle.readline()
            if not raw:
                idle_rounds += 1
                continue
            idle_rounds = 0
            line = raw.decode("utf-8", errors="replace").strip()
            match = _LISTING_ENTRY_RE.match(line)
            if match is not None:
                entries.append((match.group(1), int(match.group(2))))
        return entries

    def _read_file(self, handle: Any, name: str, size: int) -> bytes | None:
        """Envia ``storage read <path>`` y extrae exactamente ``size`` bytes.

        Se salta el echo ``COMMAND: ...\\r\\n`` buscando el magic del pcap;
        si no aparece en ``max_header`` bytes se descarta el fichero.
        """
        if size <= 0 or size > 16 * 1024 * 1024:
            logger.warning("tamano sospechoso para %s: %d", name, size)
            return None
        self._state = "leyendo"
        handle.write(f"storage read {name}\r\n".encode())
        buffer: bytes = b""
        max_header = 8192
        idle_rounds = 0
        while self._running and len(buffer) < max_header:
            piece = handle.read(4096)
            if not piece:
                idle_rounds += 1
                if idle_rounds >= _IDLE_ABORT_ROUNDS:
                    logger.warning("sin datos en la respuesta de %s; abortando", name)
                    return None
                continue
            idle_rounds = 0
            buffer += piece
            offset = buffer.find(PCAP_MAGIC)
            if offset != -1:
                break
        else:
            logger.warning("magic de pcap no encontrado en la respuesta de %s", name)
            return None
        offset = buffer.find(PCAP_MAGIC)
        data: bytes = bytes(buffer[offset : offset + size])
        idle_rounds = 0
        while self._running and len(data) < size:
            chunk: bytes = handle.read(min(4096, size - len(data)))
            if not chunk:
                idle_rounds += 1
                if idle_rounds >= _IDLE_ABORT_ROUNDS:
                    logger.warning("lectura incompleta de %s; sin mas datos", name)
                    return None
                continue
            idle_rounds = 0
            data += chunk
        if len(data) != size:
            logger.warning("lectura incompleta de %s: %d/%d bytes", name, len(data), size)
            return None
        self._state = "conectado"
        return data

    def _emit_file(self, name: str, data: bytes) -> None:
        with self._callbacks_lock:
            callbacks = list(self._file_callbacks)
        for callback in callbacks:
            try:
                callback(name, data)
            except Exception:
                logger.exception("error en el callback de ficheros de Bruce")


def _autodetect_bruce() -> str:
    from .serial_source import pick_port

    info = pick_port(None)
    if info is None:
        raise ConnectionError("no se encontro ningun puerto serial para Bruce")
    return info.device


def artifacts_dir(path: str | Path) -> Path:
    """Helper de CLI: crea el directorio de artifacts si no existe."""
    dir_path = Path(path)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path
