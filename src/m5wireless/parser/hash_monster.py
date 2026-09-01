"""Stub: ESP32-WiFi-Hash-Monster. Pendiente de fixture real de log.

Formato esperado (por documentar con una muestra real): handshakes/PMKID
asociados a BSSID, enfocado a capturas puntuales mas que a scan continuo.

Para implementarlo:
1. Añadir `tests/fixtures/hash_monster_sample.log` con lineas reales.
2. Implementar `can_parse`/`parse` sobre ese formato.
3. Aadir tests en `tests/test_parsers.py`.
"""

from __future__ import annotations

from datetime import datetime

from ..models import ObservationEvent, SourceType
from .base import AbstractParser


class HashMonsterParser(AbstractParser):
    @property
    def firmware_id(self) -> str:
        return "hash_monster"

    def can_parse(self, line: str) -> bool:
        return False

    def parse(
        self, line: str, *, received_at: datetime, source: SourceType
    ) -> ObservationEvent | None:
        raise NotImplementedError(
            "HashMonsterParser pendiente de fixture real de log; ver docstring del modulo"
        )
