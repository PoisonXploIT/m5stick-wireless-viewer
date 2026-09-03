"""Fuente de datos: Bruce por WebUI HTTP (paralela a ``BruceStorageSource``).

Mismo contrato que la fuente serial pero sin hardware en el anfitrion:

- Rutas absolutas: la WebUI del firmware opera con rutas tipo
  ``/BrucePCAP/handshakes/x.pcap`` (el JS navega desde ``/``), asi que los
  directorios por defecto llevan barra inicial.
- Poller de ``/listfiles`` por directorio cada ``poll_interval`` segundos.
- Dedup por ``(ruta, size_text)``: el listado NO trae mtime ni bytes, solo el
  tamano *legible* del firmware ("12.5 kB"). Un pcap que crece entre polls se
  re-descarga entero cuando su tamano visible cambia; la granularidad es la
  decima de la unidad (a <1024 B son bytes exactos). Aceptado y documentado:
  los pcaps de handshake son pequenos y el redownload es barato.
- ``/file ... action=download`` da bytes identicos al extraido por serial
  (validado con ``cmp``), asi que el parsing comparte ``PcapParser`` tal cual.

El canal de lineas del contrato ``AbstractSource`` queda sin uso: la WebUI no
expose consola en vivo (la shell se consulta solo via ``/cm``). El callback de
lineas se acepta y no se invoca.

Tests: ``client`` inyectable sobre ``httpx.MockTransport``; no hace falta
fake server ni hardware hasta la validacion final con el M5Stick.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable

import httpx

from ..bruce_api import BruceWebClient, BruceWebError
from .base import AbstractSource, LineCallback

logger = logging.getLogger(__name__)


class BruceWebSource(AbstractSource):
    """Bruce por WebUI HTTP: poller de ``/listfiles`` + download de pcaps."""

    def __init__(
        self,
        base_url: str,
        *,
        fs: str = "SD",
        dirs: tuple[str, ...] = ("/BrucePCAP/handshakes", "/BrucePCAP"),
        poll_interval: float = 10.0,
        file_extension: str = ".pcap",
        username: str | None = None,
        password: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url
        self._fs = fs
        self._dirs = dirs
        self._poll_interval = poll_interval
        self._file_extension = file_extension
        self._username = username
        self._password = password
        self._http_client = client
        self._owns_http = client is None
        self._timeout = timeout
        self._running = False
        self._state = "esperando"
        self._seen: dict[str, str] = {}  # ruta -> size_text del ultimo download OK
        self._file_callbacks: list[Callable[[str, bytes], None]] = []
        self._callbacks_lock = threading.Lock()
        self._client: BruceWebClient | None = None

    # ---- API publica ----
    def status(self) -> dict[str, object]:
        return {
            "state": self._state,
            "base_url": self._base_url,
            "fs": self._fs,
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
        await loop.run_in_executor(None, self._worker, callback)

    async def stop(self) -> None:
        self._running = False
        if self._client is not None:
            self._client.close()
        if self._owns_http and self._http_client is not None:
            self._http_client.close()

    # ---- worker (hilo dedicado, bloqueante) ----
    def _worker(self, line_callback: LineCallback) -> None:
        """Bucle de polling; los errores de red no matan la fuente.

        ``line_callback`` se acepta por contrato y no se invoca: la WebUI no
        tiene consola en vivo (ver docstring del modulo).
        """
        self._ensure_client()
        self._state = "conectado"
        while self._running:
            try:
                self._poll_once()
            except (BruceWebError, httpx.HTTPError) as exc:
                self._state = "error"
                logger.warning("error en el poll de la WebUI de Bruce: %s", exc)
            if not self._running:
                break
            # sleep troceado para que stop() responda en <1s.
            deadline = time.monotonic() + self._poll_interval
            while self._running and time.monotonic() < deadline:
                time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    def _ensure_client(self) -> BruceWebClient:
        if self._client is None:
            if self._http_client is None:
                self._http_client = httpx.Client(
                    base_url=self._base_url, timeout=self._timeout
                )
            self._client = BruceWebClient(
                self._base_url,
                username=self._username,
                password=self._password,
                client=self._http_client,
            )
        return self._client

    def _poll_once(self) -> None:
        """Un ciclo completo de listado + downloads (publico para tests)."""
        client = self._ensure_client()
        for directory in self._dirs:
            if not self._running:
                break
            self._state = "listando"
            entries = client.list_files(fs=self._fs, folder=directory)
            for entry in entries:
                if not self._running:
                    break
                if not entry.name.endswith(self._file_extension):
                    continue
                path = f"{directory}/{entry.name}" if directory != "/" else entry.name
                last_size = self._seen.get(path)
                if last_size == entry.size_text:
                    continue  # ya lo descargamos con este tamano visible.
                data = self._download(path)
                if data is None:
                    continue
                self._seen[path] = entry.size_text
                self._emit_file(path, data)
        if self._running:
            self._state = "conectado"

    def _download(self, path: str) -> bytes | None:
        client = self._ensure_client()
        self._state = "descargando"
        try:
            return client.download_file(path, fs=self._fs)
        except (BruceWebError, httpx.HTTPError) as exc:
            logger.warning("no se pudo descargar %s: %s", path, exc)
            return None

    def _emit_file(self, name: str, data: bytes) -> None:
        with self._callbacks_lock:
            callbacks = list(self._file_callbacks)
        for callback in callbacks:
            try:
                callback(name, data)
            except Exception:
                logger.exception("error en el callback de ficheros de BruceWebSource")
