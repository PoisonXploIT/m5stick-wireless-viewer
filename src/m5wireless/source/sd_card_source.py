"""Fuente de datos: capturas leidas directamente de una SD montada en el PC.

Denominador comun del roadmap de unificacion: Bruce, Marauder, Flipper Zero y
Hound dejan pcaps en la tarjeta SD del dispositivo; con un lector USB la
tarjeta se monta como un directorio del anfitrion y esta fuente alimenta los
mismos bytes a ``PcapParser`` que ``BruceStorageSource``/``BruceWebSource``.

- Escaneo recursivo del directorio raiz por extensiones (``.pcap``, ``.cap``).
  ``.pcapng`` se ignora a proposito: ``PcapParser`` solo soporta pcap
  clasico (linktype 105); un pcapng daria ``PcapParseError`` por magic.
- Dedup por ``(ruta, size, mtime_ns)``: el filesystem local SI da bytes y
  mtime exactos (a diferencia del listado HTTP de la WebUI). Un pcap que
  crece entre polls cambia de size/mtime y se re-lee entero; el store
  absorbe los eventos repetidos (upgrade de SSID), asi que el re-parseo es
  barato. Los pcaps de handshake son ficheros pequenos.
- Los errores de lectura (tarjeta extraida a mitad de scan, fichero en uso)
  no matan la fuente: se cuentan y se reintentan en el siguiente poll.
- El canal de lineas del contrato ``AbstractSource`` queda sin uso: la SD no
  tiene consola. El callback de lineas se acepta y no se invoca.

Tests: directorio temporal como SD falsa; no hace falta hardware (cualquier
pcap en cualquier carpeta es un caso real de esta fuente).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .base import AbstractSource, LineCallback

logger = logging.getLogger(__name__)

# (size, mtime_ns) que identifican la ultima version leida de cada fichero.
_FileKey = tuple[int, int]


class SdCardSource(AbstractSource):
    """SD montada en el PC: poller recursivo que emite capturas nuevas."""

    def __init__(
        self,
        root: str | Path,
        *,
        extensions: tuple[str, ...] = (".pcap", ".cap"),
        poll_interval: float = 5.0,
    ) -> None:
        self._root = Path(root)
        self._extensions = extensions
        self._poll_interval = poll_interval
        self._running = False
        self._state = "esperando"
        self._seen: dict[Path, _FileKey] = {}
        self._read_errors = 0
        self._file_callbacks: list[Callable[[str, bytes], None]] = []
        self._callbacks_lock = threading.Lock()

    # ---- API publica ----
    def status(self) -> dict[str, object]:
        return {
            "state": self._state,
            "root": str(self._root),
            "files_read": len(self._seen),
            "read_errors": self._read_errors,
        }

    def observe_files(self, callback: Callable[[str, bytes], None]) -> None:
        """Registra un callback de ficheros leidos ``(ruta_relativa, bytes)``.

        Se invoca desde el hilo del poller: debe ser thread-safe y rapido
        (p. ej. parsear a eventos y/o guardar artifact).
        """
        with self._callbacks_lock:
            self._file_callbacks.append(callback)

    async def start(self, callback: LineCallback) -> None:
        loop = asyncio.get_running_loop()
        self._running = True
        await loop.run_in_executor(None, self._worker, callback)

    async def stop(self) -> None:
        self._running = False

    # ---- worker (hilo dedicado, bloqueante) ----
    def _worker(self, line_callback: LineCallback) -> None:
        """Bucle de polling; los errores de lectura no matan la fuente.

        ``line_callback`` se acepta por contrato y no se invoca: la SD no
        tiene consola en vivo (ver docstring del modulo).
        """
        self._state = "conectado"
        while self._running:
            try:
                self._poll_once()
            except OSError as exc:
                self._state = "error"
                self._read_errors += 1
                logger.warning("error escaneando la SD %s: %s", self._root, exc)
            if not self._running:
                break
            # sleep troceado para que stop() responda en <1s.
            deadline = time.monotonic() + self._poll_interval
            while self._running and time.monotonic() < deadline:
                time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    def _poll_once(self) -> None:
        """Un ciclo completo de escaneo (publico para tests)."""
        if not self._root.is_dir():
            self._state = "error"
            raise FileNotFoundError(f"el directorio de la SD no existe: {self._root}")
        self._state = "listando"
        for path in sorted(self._root.rglob("*")):
            if not self._running:
                break
            if not path.is_file() or path.name.startswith("."):
                continue
            if not path.name.lower().endswith(self._extensions):
                continue
            try:
                stat = path.stat()
                data = path.read_bytes()
            except OSError as exc:
                self._read_errors += 1
                logger.warning("no se pudo leer %s: %s", path, exc)
                continue
            key = (stat.st_size, stat.st_mtime_ns)
            if self._seen.get(path) == key:
                continue  # ya leido con este size+mtime.
            self._seen[path] = key
            self._emit_file(path.relative_to(self._root).as_posix(), data)
        if self._running:
            self._state = "conectado"

    def _emit_file(self, name: str, data: bytes) -> None:
        with self._callbacks_lock:
            callbacks = list(self._file_callbacks)
        for callback in callbacks:
            try:
                callback(name, data)
            except Exception:
                logger.exception("error en el callback de ficheros de SdCardSource")
