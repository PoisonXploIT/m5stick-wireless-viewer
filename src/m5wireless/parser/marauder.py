r"""Parser de WiFi Marauder (M5StickC / ESP32).

Formato de linea (log serial de PuTTY):

    Found network: Ch: 6, RSSI: -72, BSSID: aa:bb:cc:dd:ee:01, ESSID: Movistar_1A2B

Mejora respecto a wifi-marauder-viewer: el regex original usaba
`ESSID:\s*(.+?)`, que descartaba lineas con ESSID vacio (redes ocultas).
Aqui se usa `(.*?)` y SSID vacio -> `ssid=None`.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..models import Network, NetworkSeen, ObservationEvent, SourceType
from .base import AbstractParser
from .registry import register_parser

LINE_RE = re.compile(
    r"Ch:\s*(\d+).*?RSSI:\s*(-?\d+).*?BSSID:\s*([0-9a-fA-F:]{17}).*?ESSID:\s*(.*?)\s*$",
    re.IGNORECASE,
)


@register_parser
class MarauderParser(AbstractParser):
    """Redes WiFi (Ch/RSSI/BSSID/ESSID). Stateless: una linea, un evento."""

    @property
    def firmware_id(self) -> str:
        return "marauder"

    def can_parse(self, line: str) -> bool:
        return bool(LINE_RE.search(line))

    def parse(
        self, line: str, *, received_at: datetime, source: SourceType
    ) -> ObservationEvent | None:
        match = LINE_RE.search(line.strip())
        if not match:
            return None
        channel, rssi, bssid, essid = match.groups()
        network = Network(
            bssid=bssid.lower(),
            ssid=essid or None,
            channel=int(channel),
            rssi=int(rssi),
        )
        return NetworkSeen(
            timestamp=received_at,
            firmware=self.firmware_id,
            source=source,
            raw_line=line.strip(),
            network=network,
        )
