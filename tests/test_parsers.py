"""Tests de parsers (Fase 1).

Cobertura objetivo: 100% de las lineas de cada regex, con fixtures en
tests/fixtures/. Los fixtures reproducen el formato documentado del log
serial de cada firmware.
"""

from __future__ import annotations

import pytest
from conftest import NOW

from m5wireless.models import ClientAssociated, NetworkSeen, normalize_mac
from m5wireless.parser import get_parser, registry
from m5wireless.parser.evil_m5project import EvilM5ProjectParser
from m5wireless.parser.marauder import MarauderParser

# ---------------------------------------------------------------------------
# normalize_mac
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"),
        ("aa-bb-cc-dd-ee-ff", "aa:bb:cc:dd:ee:ff"),
        ("AABB.CC.DD.EE.FF", "aa:bb:cc:dd:ee:ff"),
        ("  AABBCCDDEEFF ", "aa:bb:cc:dd:ee:ff"),
    ],
)
def test_normalize_mac_variants(raw: str, expected: str) -> None:
    assert normalize_mac(raw) == expected


@pytest.mark.parametrize("bad", ["nope", "AA:BB:CC:DD:EE", "GG:BB:CC:DD:EE:FF", ""])
def test_normalize_mac_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_mac(bad)


# ---------------------------------------------------------------------------
# MarauderParser
# ---------------------------------------------------------------------------


class TestMarauderParser:
    def test_can_parse(self, marauder_log: str) -> None:
        parser = MarauderParser()
        for line in marauder_log.splitlines():
            if line.startswith("Found network"):
                assert parser.can_parse(line)
            else:
                assert not parser.can_parse(line), f"no deberia aceptar: {line!r}"

    def test_parse_fields(self, marauder_log: str) -> None:
        parser = MarauderParser()
        line = "Found network: Ch: 6, RSSI: -72, BSSID: AA:BB:CC:DD:EE:01, ESSID: Movistar_1A2B"
        event = parser.parse(line, received_at=NOW, source="file")
        assert isinstance(event, NetworkSeen)
        assert event.firmware == "marauder"
        assert event.source == "file"
        assert event.timestamp == NOW
        net = event.network
        assert net.bssid == "aa:bb:cc:dd:ee:01"  # normalizado a minusculas
        assert net.ssid == "Movistar_1A2B"
        assert net.channel == 6
        assert net.rssi == -72

    def test_hidden_ssid_becomes_none(self, marauder_log: str) -> None:
        # Mejora respecto al repo original: ESSID vacio ya no descarta la linea.
        parser = MarauderParser()
        line = "Found network: Ch: 1, RSSI: -85, BSSID: 11:22:33:44:55:66, ESSID:"
        event = parser.parse(line, received_at=NOW, source="serial")
        assert isinstance(event, NetworkSeen)
        assert event.network.ssid is None

    def test_fixture_produces_four_events(self, marauder_log: str) -> None:
        parser = MarauderParser()
        events = [
            parser.parse(line, received_at=NOW, source="file")
            for line in marauder_log.splitlines()
            if parser.can_parse(line)
        ]
        assert len(events) == 4

    def test_malformed_rejected(self, malformed_log: str) -> None:
        parser = MarauderParser()
        for line in malformed_log.splitlines():
            assert not parser.can_parse(line)
            assert parser.parse(line, received_at=NOW, source="file") is None


# ---------------------------------------------------------------------------
# EvilM5ProjectParser
# ---------------------------------------------------------------------------


class TestEvilM5ProjectParser:
    def test_network_line(self, evil_m5project_log: str) -> None:
        parser = EvilM5ProjectParser()
        line = "[10:00:01] Movistar_1A2B (AA:BB:CC:DD:EE:01) on channel 6 has 2 clients:"
        event = parser.parse(line, received_at=NOW, source="serial")
        assert isinstance(event, NetworkSeen)
        net = event.network
        assert net.bssid == "aa:bb:cc:dd:ee:01"
        assert net.ssid == "Movistar_1A2B"
        assert net.channel == 6
        assert net.n_clients == 2
        # El timestamp de la linea se ancla a la fecha de received_at.
        assert event.timestamp.year == NOW.year
        assert event.timestamp.hour == 10
        assert event.timestamp.minute == 0
        assert event.timestamp.second == 1

    def test_client_line_inherits_last_bssid(self, evil_m5project_log: str) -> None:
        parser = EvilM5ProjectParser()
        net_line = "[10:00:01] Movistar_1A2B (AA:BB:CC:DD:EE:01) on channel 6 has 2 clients:"
        client_line = "[10:00:01] - FF:EE:DD:CC:BB:AA"
        assert parser.can_parse(net_line)
        assert parser.parse(net_line, received_at=NOW, source="file") is not None
        event = parser.parse(client_line, received_at=NOW, source="file")
        assert isinstance(event, ClientAssociated)
        client = event.client
        assert client.mac == "ff:ee:dd:cc:bb:aa"
        assert client.bssid == "aa:bb:cc:dd:ee:01"

    def test_client_before_any_network_has_none_bssid(self) -> None:
        parser = EvilM5ProjectParser()
        event = parser.parse("[10:00:01] - FF:EE:DD:CC:BB:AA", received_at=NOW, source="file")
        assert event is not None
        assert event.client.bssid is None

    def test_session_markers_rejected(self, evil_m5project_log: str) -> None:
        parser = EvilM5ProjectParser()
        for line in evil_m5project_log.splitlines():
            if "[SESSION" in line or line.startswith("="):
                assert not parser.can_parse(line)

    def test_fixture_event_counts(self, evil_m5project_log: str) -> None:
        parser = EvilM5ProjectParser()
        networks = 0
        clients = 0
        for line in evil_m5project_log.splitlines():
            if not parser.can_parse(line):
                continue
            event = parser.parse(line, received_at=NOW, source="file")
            assert event is not None
            if isinstance(event, NetworkSeen):
                networks += 1
            else:
                clients += 1
        assert networks == 2
        assert clients == 3

    def test_malformed_rejected(self, malformed_log: str) -> None:
        parser = EvilM5ProjectParser()
        for line in malformed_log.splitlines():
            assert not parser.can_parse(line)
            assert parser.parse(line, received_at=NOW, source="file") is None


# ---------------------------------------------------------------------------
# Composite / auto y registro
# ---------------------------------------------------------------------------


class TestCompositeAndRegistry:
    def test_auto_routes_mixed_lines(self, marauder_log: str, evil_m5project_log: str) -> None:
        parser = get_parser("auto")
        m_line = "Found network: Ch: 6, RSSI: -72, BSSID: AA:BB:CC:DD:EE:01, ESSID: Movistar_1A2B"
        e_line = "[10:00:01] GuestNet-5G (DE:AD:BE:EF:00:99) on channel 11 has 0 clients:"

        ev_m = parser.parse(m_line, received_at=NOW, source="file")
        assert isinstance(ev_m, NetworkSeen)
        assert ev_m.firmware == "marauder"

        ev_e = parser.parse(e_line, received_at=NOW, source="file")
        assert isinstance(ev_e, NetworkSeen)
        assert ev_e.firmware == "evil_m5project"

    def test_get_parser_known_firmwares(self) -> None:
        assert isinstance(get_parser("marauder"), MarauderParser)
        assert isinstance(get_parser("evil_m5project"), EvilM5ProjectParser)

    def test_get_parser_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            get_parser("no_existe")

    def test_registry_instances_are_fresh(self) -> None:
        # El parser de Evil-M5Project es stateful: cada get debe ser fresco.
        first = get_parser("evil_m5project")
        second = get_parser("evil_m5project")
        assert first is not second

    def test_registered_ids(self) -> None:
        assert {cls().firmware_id for cls in registry.all_factories()} == {
            "marauder",
            "evil_m5project",
            "bruce",
        }


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class TestStubs:
    @pytest.mark.parametrize(
        ("module_name", "cls"),
        [
            ("m5wireless.parser.wifi_duck", "WiFiDuckParser"),
            ("m5wireless.parser.hash_monster", "HashMonsterParser"),
            ("m5wireless.parser.packet_monitor", "PacketMonitorParser"),
        ],
    )
    def test_stubs_inert_and_documented(self, module_name: str, cls: str) -> None:
        import importlib

        module = importlib.import_module(module_name)
        parser = getattr(module, cls)()
        assert parser.can_parse("cualquier linea") is False
        with pytest.raises(NotImplementedError):
            parser.parse("x", received_at=NOW, source="file")
