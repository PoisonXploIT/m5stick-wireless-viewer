"""Parser de pcaps IEEE 802.11 capturados por Bruce (linktype 105).

No hereda de ``AbstractParser`` a proposito: su entrada es un **fichero
binario** completo, no lineas de log, asi que no entra en el registro de
parsers por linea. Lo invoca quien tenga los bytes del fichero
(``BruceStorageSource`` o un import manual).

Formato (validado con el handshake real
``data/bruce_HS_E051630EB6EA_MiFibra-B6E8.pcap``, no commiteado):

- Cabecera global pcap v2: magic ``d4c3b2a1`` (little-endian; se acepta
  tambien el big-endian ``a1b2c3d4``), version, snaplen y linktype.
  Solo se soporta **linktype 105** (``IEEE_802_11``).
- Cada registro: ts_sec/ts_usec/incl_len/orig_len + frame 802.11.

Eventos emitidos (uno por unidad nueva, en orden de aparicion):

- Frame de gestion (beacon/probe) con SSID -> ``NetworkSeen``
  (bssid = TA; el fixture real muestra un artefacto Bruce con TA
  ``ff:ff:ff:ff:*`` y en ese caso se usa SCA, que es el BSSID real).
- Frame de datos con payload EAPOL (ethertype ``0x888E``, handshake WPA)
  -> ``ClientAssociated``: cliente = la direccion unicast que NO es el AP.

Los timestamps de los eventos salen del pcap (UTC), no del reloj local. Si el
campo del dispositivo no tiene reloj sincronizado (Bruce sin NTP: ts en 1970)
o esta adelantado mas alla del margen, el timestamp se ancla al momento de
recepcion (``received_at``, inyectable para tests).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..models import (
    Client,
    ClientAssociated,
    Network,
    NetworkSeen,
    ObservationEvent,
    SourceType,
    normalize_mac,
    utc_now,
)

_MAGIC_LE = b"\xd4\xc3\xb2\xa1"
_MAGIC_BE = b"\xa1\xb2\xc3\xd4"
_LINKTYPE_IEEE_802_11 = 105
_EAPOL_ETHERTYPE = b"\x88\x8e"
# LLC/SNAP: AA AA 03 00 00 (00|FF) FF 88 8E.
_SNAP_PREFIX_RE = re.compile(rb"\xaa\xaa\x03\x00\x00[\x00\xff]\xff")
# Margin de reloj futuro tolerado antes de anclar al tiempo de recepcion.
_FUTURE_SKEW_MAX = timedelta(hours=24)


class PcapParseError(ValueError):
    """El fichero no es un pcap 802.11 soportado (magic/linktype mal)."""


@dataclass(frozen=True)
class _Frame:
    timestamp: datetime
    frame_type: int  # bits bajos de frame_control: 0 mgmt, 3 data...
    ra: str  # addr1
    ta: str  # addr2
    sca: str  # addr3
    payload: bytes  # tras cabecera (ya descontado QoS si lo hay)


def _is_broadcastish(mac: str) -> bool:
    """TA de gestion con broadcast/multicast (artefacto de captura)."""
    return mac.count("ff") >= 4


class PcapParser:
    """Convierte los bytes de un pcap Bruce en eventos normalizados."""

    def parse(
        self,
        data: bytes,
        *,
        source: SourceType = "serial",
        received_at: datetime | None = None,
    ) -> list[ObservationEvent]:
        offset = _find_magic(data)
        if offset is None:
            raise PcapParseError("magic de pcap no encontrado (d4c3b2a1)")
        endian = "<" if data[offset : offset + 4] == _MAGIC_LE else ">"
        # Cabecera global (24 B): magic(4) version(4) thiszone(4) sigfigs(4)
        # snaplen(4) linktype(4).
        linktype = struct.unpack_from(endian + "I", data, offset + 20)[0]
        if linktype != _LINKTYPE_IEEE_802_11:
            raise PcapParseError(
                f"linktype no soportado: {linktype} (solo {_LINKTYPE_IEEE_802_11}=IEEE 802.11)"
            )
        cursor = offset + 24
        received = received_at if received_at is not None else utc_now()
        events: list[ObservationEvent] = []
        # bssid -> ssid de la ultima NetworkSeen emitida (None hasta que aparece).
        seen_networks: dict[str, str | None] = {}
        seen_clients: set[tuple[str, str]] = set()
        ap_mac: str | None = None

        while cursor + 16 <= len(data):
            ts_sec, ts_usec, incl_len = struct.unpack_from(endian + "III", data, cursor)
            payload = data[cursor + 16 : cursor + 16 + incl_len]
            cursor += 16 + incl_len
            frame = _decode_frame(payload, ts_sec, ts_usec, received)
            if frame is None:
                continue
            if frame.frame_type == 0:  # gestion (beacon/probe/auth)
                event, ap_mac = self._management_event(frame, ap_mac, seen_networks, source)
                if event is not None:
                    events.append(event)
            elif frame.frame_type == 3 and ap_mac is not None:
                client = _eapol_client(frame, ap_mac)
                if client is not None:
                    key = (client, ap_mac)
                    if key not in seen_clients:
                        seen_clients.add(key)
                        events.append(
                            ClientAssociated(
                                timestamp=frame.timestamp,
                                firmware="bruce",
                                source=source,
                                raw_line=f"eapol {client} <-> {ap_mac}",
                                client=Client(mac=client, bssid=ap_mac),
                            )
                        )
        return events

    # ---- interno ----
    def _management_event(
        self,
        frame: _Frame,
        ap_mac: str | None,
        seen_networks: dict[str, str | None],
        source: SourceType,
    ) -> tuple[NetworkSeen | None, str | None]:
        ssid = _extract_ssid(frame.payload)
        bssid = frame.ta if not _is_broadcastish(frame.ta) else frame.sca
        if _is_broadcastish(bssid):
            return None, ap_mac
        if bssid != ap_mac:
            ap_mac = bssid
        # Dedup por BSSID; si la primera emision no tenia SSID y ahora si,
        # se re-emite para que el store reciba el SSID.
        if bssid in seen_networks:
            previous = seen_networks[bssid]
            if previous == ssid or ssid is None:
                return None, ap_mac
        seen_networks[bssid] = ssid
        network = Network(bssid=bssid, ssid=ssid, channel=None, rssi=None)
        return (
            NetworkSeen(
                timestamp=frame.timestamp,
                firmware="bruce",
                source=source,
                raw_line=f"mgmt {bssid} ssid={ssid!r}",
                network=network,
            ),
            ap_mac,
        )


def _find_magic(data: bytes) -> int | None:
    """Posicion del magic, tolerando basura previa (echo `COMMAND: ...`)."""
    for magic in (_MAGIC_LE, _MAGIC_BE):
        offset = data.find(magic)
        if offset != -1:
            return offset
    return None


def _sanitize_ts(ts: datetime, received: datetime) -> datetime:
    """Ancla al tiempo de recepcion si el ts del dispositivo es implausible.

    Bruce sin NTP arranca con el reloj a cero (epoch ~0 -> 1970); un reloj
    futuro mas alla del margen tampoco se puede creer. Lo que SÍ es
    plausible (año >= 2000 y no futuro) se conserva tal cual.
    """
    if ts.year < 2000 or ts > received + _FUTURE_SKEW_MAX:
        return received
    return ts


def _decode_frame(payload: bytes, ts_sec: int, ts_usec: int, received: datetime) -> _Frame | None:
    # Cabecera 802.11: frame_control(2) duration(2) RA(6) TA(6) SCA(6).
    if len(payload) < 24:
        return None
    frame_control = int.from_bytes(payload[0:2], "little")
    frame_type = frame_control & 0x03
    is_qos = (frame_control >> 2) & 0x03 == 3
    ra = normalize_mac(payload[4:10].hex(":"))
    ta = normalize_mac(payload[10:16].hex(":"))
    sca = normalize_mac(payload[16:22].hex(":"))
    body = payload[24 + (8 if is_qos else 0) :]
    timestamp = datetime.fromtimestamp(ts_sec, tz=UTC)
    if ts_usec:
        timestamp = timestamp.replace(microsecond=ts_usec % 1_000_000)
    timestamp = _sanitize_ts(timestamp, received)
    return _Frame(timestamp, frame_type, ra, ta, sca, body)


def _extract_ssid(body: bytes) -> str | None:
    """SSID (elemento id 0) del cuerpo de un frame de gestion.

    Busqueda por posicion (no regex): el primer `\\x00 <len<=32> <printable>`
    con contenido imprimible es el SSID; si no hay, red oculta -> None.
    """
    for i in range(len(body) - 2):
        if body[i] != 0:
            continue
        length = body[i + 1]
        if length == 0 or length > 32:
            continue
        candidate = body[i + 2 : i + 2 + length]
        if len(candidate) < length:
            break
        try:
            text = candidate.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if text and all(32 <= ord(c) < 127 for c in text):
            return text
    return None


def _eapol_client(frame: _Frame, ap_mac: str) -> str | None:
    """MAC del cliente en un frame EAPOL, o None si no es handshake.

    El payload de datos 802.11 empieza por LLC/SNAP; el ethertype 0x888E
    identifica EAPOL (4-way handshake WPA).
    """
    match = _SNAP_PREFIX_RE.search(frame.payload[:32])
    if match is None:
        return None
    if frame.payload[match.end() : match.end() + 2] != _EAPOL_ETHERTYPE:
        return None
    for candidate in (frame.ra, frame.ta):
        if candidate != ap_mac and not _is_broadcastish(candidate):
            return candidate
    return None
