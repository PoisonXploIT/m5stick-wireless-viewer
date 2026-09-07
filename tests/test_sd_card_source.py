"""Tests de SdCardSource (capturas desde una SD montada como directorio).

No hace falta hardware: cualquier directorio temporal con pcaps es un caso
real de esta fuente. El builder pcap es el mismo de test_pcap_parser.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from pathlib import Path

import pytest

from m5wireless.parser.pcap import PcapParser
from m5wireless.source.sd_card_source import SdCardSource

AP_MAC = "aa:bb:cc:dd:ee:01"


def _record(ts: tuple[int, int], payload: bytes) -> bytes:
    return struct.pack("<IIII", ts[0], ts[1], len(payload), len(payload)) + payload


def _mac_bytes(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def _build_beacon(ssid: str, ts: tuple[int, int]) -> bytes:
    header = (
        struct.pack("<HH", 0, 0)
        + _mac_bytes("ff:ff:ff:ff:ff:ff")  # RA
        + _mac_bytes(AP_MAC)  # TA
        + _mac_bytes(AP_MAC)  # SCA (BSSID)
        + struct.pack("<H", 0)
    )
    body = b"\x00" + bytes([len(ssid)]) + ssid.encode("utf-8")
    return _record(ts, header + body)


def _wrap_pcap(frames: list[bytes]) -> bytes:
    out = bytearray(b"\xd4\xc3\xb2\xa1")
    out += struct.pack("<HHiiII", 2, 4, 0, 0, 2500, 105)
    for frame in frames:
        out += frame
    return bytes(out)


PCAP = _wrap_pcap([_build_beacon("RedSD", (1_780_000_000, 0))])


@pytest.fixture
def sd_root(tmp_path: Path) -> Path:
    root = tmp_path / "SD"
    root.mkdir()
    return root


@pytest.fixture
def source(sd_root: Path) -> SdCardSource:
    return SdCardSource(sd_root, poll_interval=0.05)


def _collect() -> tuple[list[tuple[str, bytes]], Callable[[str, bytes], None]]:
    emitted: list[tuple[str, bytes]] = []

    def on_file(name: str, data: bytes) -> None:
        emitted.append((name, data))

    return emitted, on_file


def test_poll_reads_new_pcap_once(source: SdCardSource, sd_root: Path) -> None:
    (sd_root / "capture.pcap").write_bytes(PCAP)
    emitted, on_file = _collect()
    source.observe_files(on_file)
    source._running = True  # el worker no corre; _poll_once lo comprueba.

    source._poll_once()
    source._poll_once()  # segundo poll: dedup, no re-lee.

    assert len(emitted) == 1
    name, data = emitted[0]
    assert name == "capture.pcap"  # ruta relativa al root.
    assert data == PCAP


def test_poll_scans_subdirs_and_ignores_other_files(source: SdCardSource, sd_root: Path) -> None:
    sub = sd_root / "handshakes"
    sub.mkdir()
    (sub / "hs.pcap").write_bytes(PCAP)
    (sub / "notas.txt").write_text("hola")
    (sub / ".oculto.pcap").write_bytes(PCAP)
    emitted, on_file = _collect()
    source.observe_files(on_file)
    source._running = True

    source._poll_once()

    assert [name for name, _ in emitted] == ["handshakes/hs.pcap"]


def test_poll_rereads_when_file_grows(source: SdCardSource, sd_root: Path) -> None:
    cap = sd_root / "sniff.cap"
    cap.write_bytes(PCAP)
    emitted, on_file = _collect()
    source.observe_files(on_file)
    source._running = True

    source._poll_once()
    cap.write_bytes(PCAP + PCAP)  # el dispositivo sigue escribiendo.
    source._poll_once()

    assert len(emitted) == 2


def test_poll_skips_unreadable_file_and_continues(
    source: SdCardSource, sd_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (sd_root / "ok.pcap").write_bytes(PCAP)
    (sd_root / "roto.pcap").write_bytes(PCAP)
    real_read = Path.read_bytes

    def fake_read(self: Path) -> bytes:
        if self.name == "roto.pcap":
            raise OSError("simulado: fichero en uso")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", fake_read)
    emitted, on_file = _collect()
    source.observe_files(on_file)
    source._running = True

    source._poll_once()

    assert [name for name, _ in emitted] == ["ok.pcap"]
    assert source.status()["read_errors"] == 1


def test_status_reflects_root_and_files_read(source: SdCardSource, sd_root: Path) -> None:
    (sd_root / "a.pcap").write_bytes(PCAP)
    source._running = True
    source._poll_once()

    status = source.status()
    assert status["state"] == "conectado"
    assert status["root"] == str(sd_root)
    assert status["files_read"] == 1


def test_poll_missing_root_raises(sd_root: Path) -> None:
    source = SdCardSource(sd_root / "no_montada")
    source._running = True
    with pytest.raises(FileNotFoundError):
        source._poll_once()
    assert source.status()["state"] == "error"


def test_e2e_source_to_store(source: SdCardSource, sd_root: Path) -> None:
    """Canal completo: SD -> callback -> PcapParser -> store."""
    from datetime import UTC, datetime

    from m5wireless.parser.marauder import MarauderParser
    from m5wireless.store.memory_store import MemoryStore
    from m5wireless.worker.collector import Collector

    now = datetime(2026, 9, 7, 8, 0, 0, tzinfo=UTC)
    (sd_root / "real.pcap").write_bytes(PCAP)
    store = MemoryStore()
    collector = Collector(source, MarauderParser(), store, source_type="sdcard", clock=lambda: now)

    def handler(name: str, data: bytes) -> None:
        events = PcapParser().parse(data, source="sdcard", received_at=now)
        collector.submit_events(events)

    source.observe_files(handler)
    source._running = True
    source._poll_once()

    networks = store.get_networks()
    assert len(networks) == 1
    assert networks[0].ssid == "RedSD"
    assert collector.stats()["events"] == 1
