"""Fuente de datos: serial en vivo (pyserial), con autodeteccion y reconexion.

Diseño:

- pyserial bloquea, asi que la lectura corre en un **hilo dedicado** que publica
  cada linea a una cola ``asyncio``; :meth:`start` las entrega al callback sin
  bloquear el event loop.
- Si ``port`` es ``None`` se autodetecta con ``serial.tools.list_ports``.
- Si la conexion cae (o no hay puerto), reintenta con **backoff exponencial**
  hasta ``max_retries``.
- ``passthrough_path``: si se indica, cada linea raw tambien se escribe a ese
  archivo (para luego reproducirla con :class:`FileSource`).

``pyserial`` es una dependencia opcional (extra ``[serial]``); el modulo se
importa sin el y solo falla al intentar abrir el puerto real. Esto permite
probar la logica de lineas/reconexion inyectando un transporte falso en tests.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from .base import AbstractSource, LineCallback

logger = logging.getLogger(__name__)

_SENTINEL: object = object()


class SerialSource(AbstractSource):
    """Fuente serial en vivo con autodeteccion de puerto y reconexion."""

    def __init__(
        self,
        port: str | None = None,
        baudrate: int = 115200,
        *,
        passthrough_path: str | Path | None = None,
        max_retries: int = 5,
        base_backoff: float = 0.5,
        max_backoff: float = 30.0,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._passthrough_path = Path(passthrough_path) if passthrough_path else None
        self._max_retries = max_retries
        self._base_backoff = base_backoff
        self._max_backoff = max_backoff
        self._running = False
        self._queue: asyncio.Queue[Any] | None = None

    # ---- API publica ----
    async def start(self, callback: LineCallback) -> None:
        loop = asyncio.get_running_loop()
        self._running = True
        attempt = 0
        while self._running:
            queue: asyncio.Queue[Any] = asyncio.Queue()
            self._queue = queue
            reader = threading.Thread(target=self._reader, args=(queue, loop), daemon=True)
            reader.start()
            await self._consume(queue, callback)
            # Unir el hilo ANTES de volver a conectar o salir: asi ningun hilo
            # sobrevive a start() y llama a call_soon_threadsafe sobre un loop cerrado.
            reader.join()
            if not self._running:
                break
            attempt += 1
            if attempt > self._max_retries:
                logger.error("agotados los %d reintentos de conexion serial", self._max_retries)
                self._running = False
                break
            delay = min(self._base_backoff * (2 ** (attempt - 1)), self._max_backoff)
            logger.warning(
                "conexion serial perdida; reintento %d/%d en %.1fs",
                attempt,
                self._max_retries,
                delay,
            )
            await asyncio.sleep(delay)

    async def stop(self) -> None:
        self._running = False
        if self._queue is not None:
            # stop() corre en el mismo loop que start(): put directo.
            self._queue.put_nowait(_SENTINEL)

    # ---- internos (inyectables para tests sin hardware) ----
    def _open_port(self) -> Any:
        """Abre el puerto real. Inyectable en tests con un transporte falso."""
        try:
            import serial  # dependencia opcional; import perezoso.
        except ImportError as exc:
            raise RuntimeError("pyserial no esta instalado; instala el extra [serial]") from exc
        port = self._port if self._port is not None else _autodetect()
        return serial.Serial(port=port, baudrate=self._baudrate, timeout=1)

    def _reader(self, queue: asyncio.Queue[Any], loop: asyncio.AbstractEventLoop) -> None:
        handle: Any = None
        try:
            handle = self._open_port()
            while self._running:
                raw: bytes = handle.readline()  # b'' en timeout/sin datos.
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if not line:
                    continue
                if self._passthrough_path is not None:
                    with open(self._passthrough_path, "a", encoding="utf-8") as pf:
                        pf.write(line + "\n")
                loop.call_soon_threadsafe(queue.put_nowait, line)
        except Exception:  # cualquier fallo de puerto -> reconectar con backoff.
            logger.exception("error en lectura serial; se reintentara")
        finally:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    logger.exception("no se pudo cerrar el puerto serial")
            loop.call_soon_threadsafe(queue.put_nowait, _SENTINEL)

    async def _consume(self, queue: asyncio.Queue[Any], callback: LineCallback) -> None:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                return
            callback(item)


def _autodetect() -> str:
    from serial.tools import list_ports  # dependencia opcional; import perezoso.

    ports = list_ports.comports()
    if not ports:
        raise ConnectionError("no se encontro ningun puerto serial")
    return ports[0].device
