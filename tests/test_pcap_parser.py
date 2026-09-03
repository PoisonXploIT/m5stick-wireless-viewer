"""Tests de PcapParser (pcaps IEEE 802.11 de Bruce, linktype 105).

Dos fuentes de verdad:

- Frames sinteticos construidos con la estructura EXACTA del fixture real
  ``data/bruce_HS_*.pcap`` (no commiteado): magic LE, linktype 105, frames
  de gestion con SSID y el artefacto Bruce de TA broadcast, EAPOL en data.
- El fixture real mismo, si existe localmente (datos de casa; no commiteado).
"""

from __future__ import annotations

import glob
import struct
from datetime import UTC, datetime

import pytest

from m5wireless.models import ClientAssociated, NetworkSeen
from m5wireless.parser.pcap import PcapParseError, PcapParser

AP_MAC = "aa:bb:cc:dd:ee:01"
CLIENT_MAC = "0a:5e:1d:a6:e0:51"


# ---------------------------------------------------------------------------
# Builder sintético (misma estructura que el captureo real)
# ---------------------------------------------------------------------------


def _record(ts: tuple[int, int], payload: bytes) -> bytes:
    """Registro pcap: header de 16 bytes + frame."""
    return struct.pack("<IIII", ts[0], ts[1], len(payload), len(payload)) + payload


def _mac_bytes(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def _build_mgmt(*, ssid: str | None = None, ts: tuple[int, int]) -> bytes:
    """Beacon con el artefacto real: TA broadcast-ish, SCA = BSSID."""
    frame_control = 0  # mgmt, no QoS
    ta_bcast = "ff:ff:ff:ff:e0:51"
    header = (
        struct.pack("<HH", frame_control, 0)
        + _mac_bytes(CLIENT_MAC)  # RA
        + _mac_bytes(ta_bcast)  # TA
        + _mac_bytes(AP_MAC)  # SCA
        + struct.pack("<H", 0)  # sequence/fragment
    )
    body = b""
    if ssid is not None:
        body += b"\x00" + bytes([len(ssid)]) + ssid.encode("utf-8")
    return _record(ts, header + body)


def _build_eapol(*, ts: tuple[int, int]) -> bytes:
    """Frame de datos con LLC/SNAP + ethertype EAPOL (handshake WPA)."""
    frame_control = 3  # data, no QoS
    header = (
        struct.pack("<HH", frame_control, 0)
        + _mac_bytes(CLIENT_MAC)  # RA
        + _mac_bytes(AP_MAC)  # TA
        + _mac_bytes(CLIENT_MAC)  # SCA
        + struct.pack("<H", 0)  # sequence/fragment
    )
    # LLC: DSAP=AA SSAP=AA CTRL=03, OUI 00:00:00, proto FF + ethertype EAPOL.
    body = b"\xaa\xaa\x03\x00\x00\x00\xff" + b"\x88\x8e" + b"\x02\x03" + b"\x00" * 16
    return _record(ts, header + body)


def _wrap_pcap(frames: list[bytes]) -> bytes:
    out = bytearray()
    out += b"\xd4\xc3\xb2\xa1"
    out += struct.pack("<HHiiII", 2, 4, 0, 0, 2500, 105)
    for frame in frames:
        out += frame
    return bytes(out)


# ---------------------------------------------------------------------------
# Tests sinteticos
# ---------------------------------------------------------------------------


class TestPcapParserSynthetic:
    def test_hidden_ssid_network_seen(self) -> None:
        beacon = _build_mgmt(ts=(100, 500_000))
        events = PcapParser().parse(_wrap_pcap([beacon]))
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, NetworkSeen)
        assert event.network.bssid == AP_MAC
        assert event.network.ssid is None
        expected = datetime.fromtimestamp(100, tz=UTC).replace(microsecond=500_000)
        assert event.timestamp == expected

    def test_ssid_extracted(self) -> None:
        beacon = _build_mgmt(ssid="MiFibra-B6E8", ts=(198, 933_716))
        events = PcapParser().parse(_wrap_pcap([beacon]))
        assert len(events) == 1
        assert isinstance(events[0], NetworkSeen)
        assert events[0].network.ssid == "MiFibra-B6E8"

    def test_eapol_produces_client_associated(self) -> None:
        beacon = _build_mgmt(ssid="CafeWiFi", ts=(1, 0))
        eapol = _build_eapol(ts=(2, 0))
        events = PcapParser().parse(_wrap_pcap([beacon, eapol]))
        assert [type(e) for e in events] == [NetworkSeen, ClientAssociated]
        client_event = events[1]
        assert isinstance(client_event, ClientAssociated)
        assert client_event.client.mac == CLIENT_MAC
        assert client_event.client.bssid == AP_MAC

    def test_eapol_without_ap_context_is_skipped(self) -> None:
        # Sin frame de gestion previo no hay BSSID que atribuir.
        eapol = _build_eapol(ts=(2, 0))
        events = PcapParser().parse(_wrap_pcap([eapol]))
        assert events == []

    def test_duplicate_frames_deduplicated(self) -> None:
        beacon = _build_mgmt(ssid="Dups", ts=(1, 0))
        events = PcapParser().parse(_wrap_pcap([beacon, beacon]))
        assert len(events) == 1

    def test_command_echo_before_magic_is_tolerated(self) -> None:
        data = b"COMMAND: storage read BrucePCAP/handshakes/HS_x.pcap\r\n" + _wrap_pcap(
            [_build_mgmt(ssid="Echo", ts=(1, 0))]
        )
        events = PcapParser().parse(data)
        assert len(events) == 1

    def test_bad_magic_raises(self) -> None:
        with pytest.raises(PcapParseError):
            PcapParser().parse(b"esto no es un pcap")

    def test_wrong_linktype_raises(self) -> None:
        data = b"\xd4\xc3\xb2\xa1" + struct.pack("<HHiiII", 2, 4, 0, 0, 2500, 1)
        with pytest.raises(PcapParseError):
            PcapParser().parse(data)

    def test_source_label_propagates(self) -> None:
        events = PcapParser().parse(_wrap_pcap([_build_mgmt(ts=(1, 0))]), source="file")
        assert events[0].source == "file"


# ---------------------------------------------------------------------------
# Fixture real (solo si existe localmente; datos de casa, no commiteado)
# ---------------------------------------------------------------------------

REAL_PCAP = sorted(glob.glob("data/bruce_HS_*.pcap"))


@pytest.mark.skipif(not REAL_PCAP, reason="fixture real no presente (datos de casa)")
class TestPcapParserRealFixture:
    def test_real_handshake_parses(self) -> None:
        with open(REAL_PCAP[0], "rb") as handle:
            data = handle.read()
        events = PcapParser().parse(data)
        networks = [e for e in events if isinstance(e, NetworkSeen)]
        assert len(networks) >= 1
        # El captureo real: BSSID de la red de casa + SSID visible (el
        # artefacto TA broadcast se resuelve por SCA).
        bssids = {e.network.bssid for e in networks}
        ssids = {e.network.ssid for e in networks if e.network.ssid is not None}
        assert len(bssids) >= 1
        assert len(ssids) >= 1

    def test_real_fixture_timestamps_are_utc_aware(self) -> None:
        with open(REAL_PCAP[0], "rb") as handle:
            data = handle.read()
        events = PcapParser().parse(data)
        assert all(e.timestamp.tzinfo is not None for e in events)
