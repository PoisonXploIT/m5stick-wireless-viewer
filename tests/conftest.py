"""Fixtures compartidos de los tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from m5wireless.models import Client, ClientAssociated, Network, NetworkSeen
from m5wireless.store import MemoryStore

FIXTURES = Path(__file__).parent / "fixtures"

# Instante de referencia para todos los tests (determinista).
NOW = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def marauder_log() -> str:
    return (FIXTURES / "marauder_scan.log").read_text(encoding="utf-8")


@pytest.fixture
def marauder_log_path() -> Path:
    return FIXTURES / "marauder_scan.log"


@pytest.fixture
def evil_m5project_log_path() -> Path:
    return FIXTURES / "evil_m5project_scan.log"


@pytest.fixture
def malformed_log_path() -> Path:
    return FIXTURES / "malformed_lines.log"


@pytest.fixture
def evil_m5project_log() -> str:
    return (FIXTURES / "evil_m5project_scan.log").read_text(encoding="utf-8")


@pytest.fixture
def malformed_log() -> str:
    return (FIXTURES / "malformed_lines.log").read_text(encoding="utf-8")


def _seeded_events() -> list[NetworkSeen | ClientAssociated]:
    """Semilla determinista: 3 redes, 2 clientes, 6 observaciones."""
    t1 = NOW - timedelta(hours=2)
    t2 = NOW - timedelta(hours=1)

    def net(bssid: str, ssid: str | None, channel: int, rssi: int) -> Network:
        return Network(bssid=bssid, ssid=ssid, channel=channel, rssi=rssi)

    AA = "aa:bb:cc:dd:ee:01"
    BB = "11:22:33:44:55:66"
    DE = "de:ad:be:ef:00:99"
    return [
        NetworkSeen(
            timestamp=t1, firmware="marauder", source="file",
            raw_line="aa line 1", network=net(AA, "Movistar_1A2B", 6, -55),
        ),
        NetworkSeen(
            timestamp=NOW, firmware="marauder", source="file",
            raw_line="aa line 2", network=net(AA, "Movistar_1A2B", 6, -70),
        ),
        NetworkSeen(
            timestamp=t2, firmware="marauder", source="file",
            raw_line="bb line", network=net(BB, None, 1, -80),
        ),
        NetworkSeen(
            timestamp=NOW, firmware="evil_m5project", source="serial",
            raw_line="de line", network=net(DE, "CafeWiFi", 11, -72),
        ),
        ClientAssociated(
            timestamp=t1, firmware="marauder", source="file",
            raw_line="c1 line",
            client=Client(mac="ff:ee:dd:cc:bb:aa", bssid=AA),
        ),
        ClientAssociated(
            timestamp=NOW, firmware="evil_m5project", source="serial",
            raw_line="c2 line",
            client=Client(mac="00:11:22:33:44:55", bssid=DE),
        ),
    ]


@pytest.fixture
def seeded_store() -> MemoryStore:
    """MemoryStore con la semilla determinista aplicada via `apply`."""
    store = MemoryStore()
    for event in _seeded_events():
        store.apply(event)
    return store
