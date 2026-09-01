"""Tests de integracion: FileSource -> parser (registro) -> store, end-to-end."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from m5wireless.models import SourceType
from m5wireless.parser import get_parser
from m5wireless.source import FileSource
from m5wireless.store import AbstractStore, MemoryStore
from m5wireless.worker import Collector

NOW = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)


def _run_file(
    path: Path,
    *,
    firmware: str = "auto",
    source_type: SourceType = "file",
) -> tuple[AbstractStore, Collector]:
    store = MemoryStore()
    source = FileSource(path, follow=False)
    collector = Collector(
        source, get_parser(firmware), store, source_type=source_type, clock=lambda: NOW
    )
    asyncio.run(collector.run())
    return store, collector


def test_marauder_end_to_end(marauder_log_path: Path, marauder_log: str) -> None:
    store, collector = _run_file(marauder_log_path, firmware="marauder")

    networks = {n.bssid: n for n in store.get_networks()}
    assert set(networks) == {"aa:bb:cc:dd:ee:01", "11:22:33:44:55:66", "de:ad:be:ef:00:99"}

    # La red repetida queda con el ultimo RSSI (-70) y la primera vez vista.
    aa = networks["aa:bb:cc:dd:ee:01"]
    assert aa.ssid == "Movistar_1A2B"
    assert aa.rssi == -70
    assert aa.first_seen == NOW and aa.last_seen == NOW

    # Red oculta: ESSID vacio -> ssid None.
    assert networks["11:22:33:44:55:66"].ssid is None

    # Ningun cliente en el log de Marauder.
    assert store.get_clients() == []

    stats = collector.stats()
    assert stats["events"] == 4
    assert stats["errors"] == 0
    assert stats["lines"] == len(marauder_log.splitlines())


def test_evil_m5project_end_to_end(evil_m5project_log_path: Path) -> None:
    store, collector = _run_file(evil_m5project_log_path, firmware="evil_m5project")

    networks = {n.bssid: n for n in store.get_networks()}
    assert set(networks) == {"aa:bb:cc:dd:ee:01", "de:ad:be:ef:00:99"}

    clients = {c.mac: c for c in store.get_clients()}
    # El cliente ff:.. aparece primero en AA y luego se reasocia a DE:AD.
    assert clients["ff:ee:dd:cc:bb:aa"].bssid == "de:ad:be:ef:00:99"
    assert clients["00:11:22:33:44:55"].bssid == "aa:bb:cc:dd:ee:01"

    # n_clients se calcula sobre las asociaciones reales observadas.
    assert networks["aa:bb:cc:dd:ee:01"].n_clients == 1
    assert networks["de:ad:be:ef:00:99"].n_clients == 1

    stats = collector.stats()
    assert stats["events"] == 5  # 2 lineas de red + 3 de cliente.
    assert stats["errors"] == 0


def test_malformed_lines_produce_no_events(malformed_log_path: Path) -> None:
    store, collector = _run_file(malformed_log_path, firmware="auto")

    assert store.get_networks() == []
    assert store.get_clients() == []
    stats = collector.stats()
    assert stats["events"] == 0
    assert stats["errors"] == 0


def test_composite_routes_mixed_firmware(tmp_path: Path, marauder_log: str, evil_m5project_log: str) -> None:
    mixed = tmp_path / "mixed.log"
    mixed.write_text(marauder_log + "\n" + evil_m5project_log, encoding="utf-8")

    store, collector = _run_file(mixed, firmware="auto")

    # Redes de ambos firmwares conviven en el mismo store.
    networks = {n.bssid: n for n in store.get_networks()}
    assert "aa:bb:cc:dd:ee:01" in networks  # aparece en los dos logs.
    assert "de:ad:be:ef:00:99" in networks
    assert "11:22:33:44:55:66" in networks

    # El historico conserva el firmware de origen por linea.
    history = store.get_network_history("aa:bb:cc:dd:ee:01")
    firmwares = {row.firmware for row in history}
    assert {"marauder", "evil_m5project"} <= firmwares

    assert collector.stats()["errors"] == 0
