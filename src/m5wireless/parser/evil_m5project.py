"""Parser de Evil-M5Project (M5Stick Plus 2).

Formato del log:

    [SESSION START] 2026-01-15T10:00:00 | COM3 @ 115200
    [10:00:01] Movistar_1A2B (aa:bb:cc:dd:ee:01) on channel 6 has 2 clients:
    [10:00:01] - ff:ee:dd:cc:bb:aa
    [SESSION END] 2026-01-15T10:05:00 | 120 lineas capturadas

Notas:
- Las lineas de red llevan su propio timestamp `[ts]`; si no se puede
  interpretar, se usa `received_at`.
- Las lineas de cliente (`[ts] - MAC`) heredan el BSSID de la ultima linea
  de red vista en este parser (estado interno). Si aun no se ha visto ninguna
  red, el evento sale con `bssid=None`.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..models import (
    Client,
    ClientAssociated,
    Network,
    NetworkSeen,
    ObservationEvent,
    SourceType,
)
from .base import AbstractParser
from .registry import register_parser

NETWORK_RE = re.compile(
    r"\[(.*?)\] (.+) \(([0-9a-fA-F:]{17})\) on channel (\d+) has (\d+) clients:"
)
CLIENT_RE = re.compile(r"^\[(.*?)\] - ([0-9a-fA-F:]{17})\s*$")

_TS_FORMATS = ("%H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


def _line_timestamp(raw_ts: str, received_at: datetime) -> datetime:
    """Interpreta el timestamp de la linea; fallback a `received_at`."""
    for fmt in _TS_FORMATS:
        try:
            parsed = datetime.strptime(raw_ts.strip(), fmt)  # noqa: DTZ007
        except ValueError:
            continue
        if fmt == "%H:%M:%S":
            # Solo hora: strptime devuelve 1900-01-01; se ancla a la fecha
            # de received_at.
            return parsed.replace(
                year=received_at.year,
                month=received_at.month,
                day=received_at.day,
                tzinfo=received_at.tzinfo,
            )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=received_at.tzinfo)
        return parsed
    return received_at


@register_parser
class EvilM5ProjectParser(AbstractParser):
    """Redes + clientes asociados. Stateful: recuerda el ultimo BSSID."""

    def __init__(self) -> None:
        self._last_bssid: str | None = None

    @property
    def firmware_id(self) -> str:
        return "evil_m5project"

    def can_parse(self, line: str) -> bool:
        if line.startswith("=") or "[SESSION" in line:
            return False
        return bool(NETWORK_RE.search(line) or CLIENT_RE.match(line))

    def parse(
        self, line: str, *, received_at: datetime, source: SourceType
    ) -> ObservationEvent | None:
        stripped = line.strip()
        net_match = NETWORK_RE.search(stripped)
        if net_match:
            ts_raw, ssid, bssid, channel, n_clients = net_match.groups()
            self._last_bssid = bssid.lower()
            network = Network(
                bssid=self._last_bssid,
                ssid=ssid or None,
                channel=int(channel),
                rssi=None,
                n_clients=int(n_clients),
            )
            return NetworkSeen(
                timestamp=_line_timestamp(ts_raw, received_at),
                firmware=self.firmware_id,
                source=source,
                raw_line=stripped,
                network=network,
            )

        client_match = CLIENT_RE.match(stripped)
        if client_match:
            ts_raw, mac = client_match.groups()
            client = Client(mac=mac.lower(), bssid=self._last_bssid)
            return ClientAssociated(
                timestamp=_line_timestamp(ts_raw, received_at),
                firmware=self.firmware_id,
                source=source,
                raw_line=stripped,
                client=client,
            )

        return None


