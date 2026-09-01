"""Stub: PacketMonitor (ESP32). Pendiente de fixture real de log.

Formato esperado (por documentar con una muestra real): contadores de
paquetes por canal/BSSID, orientado a estadisticas de trafico.

Para implementarlo:
1. Añadir `tests/fixtures/packet_monitor_sample.log` con lineas reales.
2. Implementar `can_parse`/`parse` sobre ese formato.
3. Aadir tests en `tests/test_parsers.py`.
"""

from __future__ import annotations

from datetime import datetime

from ..models import ObservationEvent, SourceType
from .base import AbstractParser


class PacketMonitorParser(AbstractParser):
    @property
    def firmware_id(self) -> str:
        return "packet_monitor"

    def can_parse(self, line: str) -> bool:
        return False

    def parse(
        self, line: str, *, received_at: datetime, source: SourceType
    ) -> ObservationEvent | None:
        raise NotImplementedError(
            "PacketMonitorParser pendiente de fixture real de log; ver docstring del modulo"
        )
