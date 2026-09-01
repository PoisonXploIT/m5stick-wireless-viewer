"""Stub: WiFi Duck. Pendiente de fixture real de log.

Formato esperado (por documentar con una muestra real): pares SSID/BSSID y,
opcionalmente, keystrokes capturados por el gadget.

Para implementarlo:
1. Añadir `tests/fixtures/wifi_duck_sample.log` con lineas reales.
2. Implementar `can_parse`/`parse` sobre ese formato.
3. Aadir tests en `tests/test_parsers.py`.
"""

from __future__ import annotations

from datetime import datetime

from ..models import ObservationEvent, SourceType
from .base import AbstractParser


class WiFiDuckParser(AbstractParser):
    @property
    def firmware_id(self) -> str:
        return "wifi_duck"

    def can_parse(self, line: str) -> bool:
        return False

    def parse(
        self, line: str, *, received_at: datetime, source: SourceType
    ) -> ObservationEvent | None:
        raise NotImplementedError(
            "WiFiDuckParser pendiente de fixture real de log; ver docstring del modulo"
        )
