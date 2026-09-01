"""Store en memoria: sin persistencia, para desarrollo y demos."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..models import Client, Network, ObservationEvent, utc_now
from .base import AbstractStore, ObservationRow, event_to_observation_row


class MemoryStore(AbstractStore):
    """Estado actual en dicts + historico append-only en una lista."""

    def __init__(self) -> None:
        self._networks: dict[str, Network] = {}
        self._clients: dict[str, Client] = {}
        self._observations: list[ObservationRow] = []

    # ---- escritura ----
    def upsert_network(self, network: Network, *, at: datetime) -> None:
        existing = self._networks.get(network.bssid)
        if existing is None:
            self._networks[network.bssid] = Network(
                bssid=network.bssid,
                ssid=network.ssid,
                channel=network.channel,
                rssi=network.rssi,
                first_seen=at,
                last_seen=at,
            )
        else:
            if network.ssid is not None:
                existing.ssid = network.ssid
            if network.channel is not None:
                existing.channel = network.channel
            if network.rssi is not None:
                existing.rssi = network.rssi
            existing.last_seen = at

    def upsert_client(self, client: Client, *, at: datetime) -> None:
        existing = self._clients.get(client.mac)
        if existing is None:
            self._clients[client.mac] = Client(
                mac=client.mac, bssid=client.bssid, first_seen=at, last_seen=at
            )
        else:
            # `Client` es frozen: se reconstruye con el estado actualizado.
            new_bssid = client.bssid if client.bssid is not None else existing.bssid
            self._clients[client.mac] = Client(
                mac=existing.mac,
                bssid=new_bssid,
                first_seen=existing.first_seen,
                last_seen=at,
            )

    def record_observation(self, event: ObservationEvent) -> None:
        self._observations.append(event_to_observation_row(event))

    # ---- consulta ----
    def get_networks(
        self, *, since: datetime | None = None, until: datetime | None = None
    ) -> list[Network]:
        result: list[Network] = []
        for net in self._networks.values():
            if since is not None and net.last_seen < since:
                continue
            if until is not None and net.last_seen > until:
                continue
            clients = {c.mac for c in self._clients.values() if c.bssid == net.bssid}
            result.append(
                Network(
                    bssid=net.bssid,
                    ssid=net.ssid,
                    channel=net.channel,
                    rssi=net.rssi,
                    n_clients=len(clients),
                    clients=clients,
                    first_seen=net.first_seen,
                    last_seen=net.last_seen,
                )
            )
        return result

    def get_clients(self, *, associated_to: str | None = None) -> list[Client]:
        out: list[Client] = []
        for c in self._clients.values():
            if associated_to is not None and c.bssid != associated_to:
                continue
            out.append(
                Client(mac=c.mac, bssid=c.bssid, first_seen=c.first_seen, last_seen=c.last_seen)
            )
        return out

    def get_network_history(
        self,
        bssid: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[ObservationRow]:
        rows: list[ObservationRow] = []
        for row in self._observations:
            if row.bssid != bssid:
                continue
            if since is not None and row.timestamp < since:
                continue
            if until is not None and row.timestamp > until:
                continue
            rows.append(row)
        return rows

    def get_channel_distribution(self) -> dict[int, int]:
        dist: dict[int, int] = {}
        for net in self._networks.values():
            if net.channel is not None:
                dist[net.channel] = dist.get(net.channel, 0) + 1
        return dist

    # ---- mantenimiento ----
    def prune_older_than(self, days: int, *, reference: datetime | None = None) -> int:
        ref = reference if reference is not None else utc_now()
        cutoff = ref - timedelta(days=days)
        before = len(self._observations)
        self._observations = [r for r in self._observations if r.timestamp >= cutoff]
        return before - len(self._observations)
