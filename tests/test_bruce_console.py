"""Tests de BruceConsoleParser (consola Bruce, CLI tipo Flipper).

El fixture ``tests/fixtures/bruce_console.log`` es una copia del captureo
real ``data/bruce_capture.log`` (no commiteado por contener datos reales);
el contenido son solo lineas de ciclo de vida, sin MACs ni IPs.
"""

from __future__ import annotations

from conftest import NOW

from m5wireless.models import StatusEvent
from m5wireless.parser import get_parser
from m5wireless.parser.bruce_console import BruceConsoleParser


class TestBruceConsoleParser:
    def test_fixture_recognized_lines(self, bruce_console_log: str) -> None:
        # 5 de las 6 lineas del fixture son eventos; la informativa
        # "SDCard in a different Bus..." no emite (pendiente de decidir).
        parser = BruceConsoleParser()
        recognized = [line for line in bruce_console_log.splitlines() if parser.can_parse(line)]
        assert len(recognized) == 5

    def test_selected_event(self, bruce_console_log: str) -> None:
        parser = BruceConsoleParser()
        event = parser.parse("[   7.8s] Selected: Sniffer", received_at=NOW, source="serial")
        assert isinstance(event, StatusEvent)
        assert event.firmware == "bruce"
        assert event.source == "serial"
        assert event.timestamp == NOW
        assert "Sniffer" in event.message

    def test_sniffer_started_event(self) -> None:
        parser = BruceConsoleParser()
        event = parser.parse("[   9.1s] Sniffer started!", received_at=NOW, source="serial")
        assert isinstance(event, StatusEvent)
        assert "sniffer iniciado" in event.message

    def test_sdcard_not_mounted_event(self) -> None:
        parser = BruceConsoleParser()
        event = parser.parse(
            "[   8.9s] SDCARD NOT mounted, check wiring and format",
            received_at=NOW,
            source="serial",
        )
        assert isinstance(event, StatusEvent)
        assert "SD no montada" in event.message

    def test_espidf_error_event(self) -> None:
        parser = BruceConsoleParser()
        line = (
            "[   8.4s] [1845679][E][sd_diskio.cpp:761] "
            "sdcard_mount(): f_mount failed: (3) The physical drive cannot work"
        )
        event = parser.parse(line, received_at=NOW, source="serial")
        assert isinstance(event, StatusEvent)
        assert "f_mount failed" in event.message

    def test_fixture_produces_five_status_events(self, bruce_console_log: str) -> None:
        # El fixture real tiene 6 lineas; 5 emiten eventos (Selected, 2 errores
        # IDF, SD no montada, Sniffer started). Solo "SDCard in a different
        # Bus..." no emite.
        parser = BruceConsoleParser()
        events = [
            e
            for e in (
                parser.parse(line, received_at=NOW, source="serial")
                for line in bruce_console_log.splitlines()
            )
            if e is not None
        ]
        assert len(events) == 5
        assert all(isinstance(e, StatusEvent) for e in events)
        messages = " | ".join(e.message for e in events)
        assert "Selected" in messages or "seleccionado" in messages
        assert "sniffer iniciado" in messages
        assert "SD no montada" in messages

    def test_unrecognized_lines_return_none(self) -> None:
        parser = BruceConsoleParser()
        for line in ("", "   ", "> ", "[   1.0s] some random noise"):
            assert parser.can_parse(line) is False
            assert parser.parse(line, received_at=NOW, source="serial") is None

    def test_prefix_is_optional(self) -> None:
        # Bruce a veces escribe sin el prefijo de tiempo.
        parser = BruceConsoleParser()
        event = parser.parse("Sniffer started!", received_at=NOW, source="serial")
        assert isinstance(event, StatusEvent)


def test_registered_in_registry() -> None:
    parser = get_parser("bruce")
    assert isinstance(parser, BruceConsoleParser)
    assert parser.firmware_id == "bruce"


def test_status_event_flows_to_observer_without_store_side_effects() -> None:
    from m5wireless.store import MemoryStore
    from m5wireless.worker.collector import Collector

    store = MemoryStore()
    seen: list[object] = []
    collector = Collector(
        source=None,  # type: ignore[arg-type] - no se usa en este test.
        parser=BruceConsoleParser(),
        store=store,
    )
    collector.observe(seen.append)
    # Se inyecta el evento directamente (el canal de lineas ya esta cubierto
    # por los tests de fixture); aqui importa el tratamiento del colector.
    from m5wireless.models import StatusEvent

    event = StatusEvent(
        timestamp=NOW,
        firmware="bruce",
        source="serial",
        raw_line="[   9.1s] Sniffer started!",
        message="sniffer iniciado",
    )
    collector.submit_events([event])
    assert seen == [event]
    # El store no persiste eventos de estado.
    assert list(store.iter_observations()) == []
