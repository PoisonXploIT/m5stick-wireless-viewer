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
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import AbstractSource, LineCallback

logger = logging.getLogger(__name__)

_SENTINEL: object = object()


@dataclass(frozen=True)
class PortInfo:
    """Puerto serie enumerado (wrapper minimo sobre ``serial.tools.list_ports``)."""

    device: str
    description: str
    vid: str | None = None
    pid: str | None = None


def _vid_pid_from_hwid(hwid: str) -> tuple[str | None, str | None]:
    """Extrae VID/PID del campo ``hwid`` (p. ej. ``USB VID:PID=2E8A:0003``)."""
    match = re.search(r"VID:PID=([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})", hwid)
    if match is None:
        return None, None
    return match.group(1).upper(), match.group(2).upper()


def list_ports() -> list[PortInfo]:
    """Lista los puertos serie disponibles (pyserial, import perezoso)."""
    from serial.tools import list_ports as _list  # dependencia opcional.

    ports: list[PortInfo] = []
    for port in _list.comports():
        vid, pid = _vid_pid_from_hwid(str(port.hwid or ""))
        ports.append(
            PortInfo(
                device=port.device,
                description=str(port.description or ""),
                vid=vid,
                pid=pid,
            )
        )
    return ports


def port_hint(info: PortInfo) -> str | None:
    """Pista de que firmware/placa hay detras del puerto (o ``None``).

    La M5StickC aparece como "USB Serial (COMx)" con VID 0x2E8A, asi que el
    match va por VID tanto como por descripcion.
    """
    text = info.description.lower()
    vid = (info.vid or "").lower()
    if "m5stack" in text or vid == "2e8a":
        return "posible M5Stick (M5Stack)"
    if "esp32" in text or "usb jtag" in text:
        return "ESP32 (CDC/JTAG)"
    if "cp210x" in text or "cp2102" in text:
        return "CP210x (UART)"
    if "ch340" in text or "ch341" in text:
        return "CH34x (UART)"
    if "cdc acm" in text or "usb serial" in text:
        return "CDC generico"
    return None


def pick_port(preferred: str | None = None) -> PortInfo | None:
    """Elige el puerto para autodeteccion.

    Con ``preferred`` (p. ej. ``COM3``) se devuelve ese dispositivo sin validar;
    sin preferido, prefiere puertos con pista M5Stick/ESP32 y si no, el primero.
    Devuelve ``None`` solo cuando no hay ningun puerto disponible.
    """
    if preferred is not None:
        return PortInfo(device=preferred, description="(especificado por el usuario)")
    ports = list_ports()
    if not ports:
        return None
    for info in ports:
        hint = port_hint(info)
        if hint is not None and ("M5Stick" in hint or "ESP32" in hint):
            return info
    return ports[0]


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
        self._state = "esperando"
        self._last_port: str | None = port

    # ---- API publica ----
    def status(self) -> dict[str, object]:
        """Estado de conexion para el dashboard (thread-safe a efectos practicos:
        escrituras simples desde el hilo lector)."""
        return {
            "state": self._state,
            "port": self._last_port,
            "baudrate": self._baudrate,
        }

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
            self._state = "conectado"
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
            self._state = "reconectando"
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
    info = pick_port(None)
    if info is None:
        raise ConnectionError("no se encontro ningun puerto serial")
    return info.device
