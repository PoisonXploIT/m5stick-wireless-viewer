"""Fuente de datos: leer lineas de un archivo (logs existentes / fixtures)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from .base import AbstractSource, LineCallback


class FileSource(AbstractSource):
    """Lee lineas de un archivo.

    - ``follow=True``  -> modo *tail -f*: salta al final del fichero y sigue
      las nuevas lineas hasta que se llame a :meth:`stop`. Util para reproducir
      un log en vivo (p. ej. el passthrough de la fuente serial).
    - ``follow=False`` -> reproduccion unica: lee el contenido existente hasta
      EOF y termina sola. Es el modo para fixtures y demo/offline.

    Soporta rotacion de logs de forma sencilla: si el fichero se re-crea, las
    lineas nuevas que aparezcan en la cola siguen entregandose (en ``follow``).
    """

    def __init__(
        self, path: str | Path, *, follow: bool = True, poll_interval: float = 0.1
    ) -> None:
        self._path = Path(path)
        self._follow = follow
        self._poll_interval = poll_interval
        self._running = False
        self._state = "esperando"

    def status(self) -> dict[str, object]:
        return {"state": self._state, "path": str(self._path)}

    async def start(self, callback: LineCallback) -> None:
        self._running = True
        self._state = "reproduciendo"
        # Lectura bloqueante a proposito: FileSource es de un solo consumidor y su
        # callback es sincrono por contrato; el modo principal es reproduccion.
        with open(self._path, "r", encoding="utf-8", errors="replace") as handle:  # noqa: ASYNC230
            if self._follow:
                handle.seek(0, 2)  # SEEK_END: no re-leer lo que ya hay.
            while self._running:
                line = handle.readline()
                if line:
                    callback(line.rstrip("\n"))
                elif not self._follow:
                    break  # EOF en modo reproduccion: terminar.
                else:
                    await asyncio.sleep(self._poll_interval)

    async def stop(self) -> None:
        self._running = False
