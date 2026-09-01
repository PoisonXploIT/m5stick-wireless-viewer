"""Esquemas Pydantic de la API web (Fase 3).

Todos los `datetime` son timezone-aware (UTC) en origen y se serializan en
ISO-8601. Los builders (`network_read`, `client_read`, ...) convierten los
dataclasses del dominio a los modelos de respuesta, de modo que las rutas no
construyen dicts a mano.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from ..models import Client, ClientAssociated, Network, NetworkSeen, ObservationEvent
from ..store.base import ObservationRow


class NetworkRead(BaseModel):
    """Red actual (estado, deduplicada por BSSID)."""

    bssid: str
    ssid: str | None
    channel: int | None
    rssi: int | None
    n_clients: int
    client_macs: list[str]
    first_seen: datetime
    last_seen: datetime


class ClientRead(BaseModel):
    """Cliente actual (estado, deduplicado por MAC)."""

    mac: str
    bssid: str | None
    first_seen: datetime
    last_seen: datetime


class HistoryRow(BaseModel):
    """Fila del historico append-only (tabla `observations`)."""

    timestamp: datetime
    firmware: str
    source: str
    event_type: str
    bssid: str | None
    rssi: int | None
    client_mac: str | None


class NetworkDetail(NetworkRead):
    """Detalle de una red: estado + clientes asociados + historico."""

    clients: list[ClientRead]
    history: list[HistoryRow]


class NetworkListResponse(BaseModel):
    networks: list[NetworkRead]


class CollectorStats(BaseModel):
    lines: int
    events: int
    errors: int


class HealthResponse(BaseModel):
    status: str
    store: str
    source: str | None
    collector: CollectorStats | None
    networks: int
    clients: int


class ChannelDistributionResponse(BaseModel):
    channels: dict[int, int]


# ---- builders dominio -> esquema ----


def network_read(net: Network) -> NetworkRead:
    return NetworkRead(
        bssid=net.bssid,
        ssid=net.ssid,
        channel=net.channel,
        rssi=net.rssi,
        n_clients=net.n_clients,
        client_macs=sorted(net.clients),
        first_seen=net.first_seen,
        last_seen=net.last_seen,
    )


def client_read(client: Client) -> ClientRead:
    return ClientRead(
        mac=client.mac,
        bssid=client.bssid,
        first_seen=client.first_seen,
        last_seen=client.last_seen,
    )


def history_row(row: ObservationRow) -> HistoryRow:
    return HistoryRow(
        timestamp=row.timestamp,
        firmware=row.firmware,
        source=row.source,
        event_type=row.event_type,
        bssid=row.bssid,
        rssi=row.rssi,
        client_mac=row.client_mac,
    )


def network_detail(
    net: Network, clients: list[Client], history: list[ObservationRow]
) -> NetworkDetail:
    base = network_read(net)
    return NetworkDetail(
        **base.model_dump(),
        clients=[client_read(c) for c in clients],
        history=[history_row(r) for r in history],
    )


def event_to_json(event: ObservationEvent) -> dict[str, object]:
    """Serializa un `ObservationEvent` como dict plano para SSE.

    Union discriminada por el campo `event`:
    - 'network_seen'       -> datos de la red
    - 'client_associated' -> datos del cliente
    """
    if isinstance(event, NetworkSeen):
        return {
            "event": "network_seen",
            "timestamp": event.timestamp.isoformat(),
            "firmware": event.firmware,
            "source": event.source,
            "bssid": event.network.bssid,
            "ssid": event.network.ssid,
            "channel": event.network.channel,
            "rssi": event.network.rssi,
        }
    if isinstance(event, ClientAssociated):
        return {
            "event": "client_associated",
            "timestamp": event.timestamp.isoformat(),
            "firmware": event.firmware,
            "source": event.source,
            "mac": event.client.mac,
            "bssid": event.client.bssid,
        }
    raise TypeError(f"evento desconocido: {event!r}")
