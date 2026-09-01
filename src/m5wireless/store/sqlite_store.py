"""Store SQLite (default): persistencia local en un fichero.

Esquema (plan §8.2) con dos matizs acordados:

- `clients` es el **estado actual** (PK por mac); la traza completa de
  asociaciones vive en `observations`.
- Indexes en ``observations.timestamp`` y ``networks.last_seen``.

Desviaciones documentadas respecto al esquema propuesto:

- Se omite la columna ``networks.n_clients``: se calcula en lectura a partir de
  ``clients`` (evita mantener un contador denormalizado que no se puede
  actualizar de forma fiable).
- La FK ``clients.bssid -> networks.bssid`` se declara pero NO se fuerza
  (sin ``PRAGMA foreign_keys=ON``): un cliente puede referenciar una red que
  aun no ha aparecido como linea de red, y la insercion no debe fallar.

Los timestamps se guardan como texto ISO-8601 UTC; al ser todos el mismo
formato, el orden lexicografico coincide con el cronologico.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

from ..models import Client, Network, ObservationEvent, utc_now
from .base import AbstractStore, ObservationRow, event_to_observation_row

_SCHEMA = """
CREATE TABLE IF NOT EXISTS networks (
    bssid TEXT PRIMARY KEY,
    ssid TEXT,
    channel INTEGER,
    rssi INTEGER,
    first_seen TIMESTAMP,
    last_seen TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_networks_last_seen ON networks(last_seen);

CREATE TABLE IF NOT EXISTS clients (
    mac TEXT PRIMARY KEY,
    bssid TEXT,
    first_seen TIMESTAMP,
    last_seen TIMESTAMP,
    FOREIGN KEY (bssid) REFERENCES networks(bssid)
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP,
    firmware TEXT,
    source TEXT,
    event_type TEXT,
    bssid TEXT,
    rssi INTEGER,
    client_mac TEXT,
    raw_line TEXT
);
CREATE INDEX IF NOT EXISTS idx_observations_timestamp ON observations(timestamp);
"""


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SQLiteStore(AbstractStore):
    """Persistencia local en SQLite (stdlib, sin dependencias extra)."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- escritura ----
    def upsert_network(self, network: Network, *, at: datetime) -> None:
        self._conn.execute(
            """
            INSERT INTO networks (bssid, ssid, channel, rssi, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(bssid) DO UPDATE SET
                ssid = COALESCE(excluded.ssid, networks.ssid),
                channel = COALESCE(excluded.channel, networks.channel),
                rssi = COALESCE(excluded.rssi, networks.rssi),
                last_seen = excluded.last_seen
            """,
            (
                network.bssid,
                network.ssid,
                network.channel,
                network.rssi,
                _iso(at),
                _iso(at),
            ),
        )
        self._conn.commit()

    def upsert_client(self, client: Client, *, at: datetime) -> None:
        self._conn.execute(
            """
            INSERT INTO clients (mac, bssid, first_seen, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(mac) DO UPDATE SET
                bssid = COALESCE(excluded.bssid, clients.bssid),
                last_seen = excluded.last_seen
            """,
            (client.mac, client.bssid, _iso(at), _iso(at)),
        )
        self._conn.commit()

    def record_observation(self, event: ObservationEvent) -> None:
        row = event_to_observation_row(event)
        self._conn.execute(
            """
            INSERT INTO observations
                (timestamp, firmware, source, event_type, bssid, rssi, client_mac, raw_line)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _iso(row.timestamp),
                row.firmware,
                row.source,
                row.event_type,
                row.bssid,
                row.rssi,
                row.client_mac,
                row.raw_line,
            ),
        )
        self._conn.commit()

    # ---- consulta ----
    def get_network(self, bssid: str) -> Network | None:
        cursor = self._conn.execute("SELECT * FROM networks WHERE bssid = ?", (bssid,))
        row = cursor.fetchone()
        if row is None:
            return None
        clients_cursor = self._conn.execute(
            "SELECT mac FROM clients WHERE bssid = ?", (bssid,)
        )
        clients = {r["mac"] for r in clients_cursor.fetchall()}
        return Network(
            bssid=row["bssid"],
            ssid=row["ssid"],
            channel=row["channel"],
            rssi=row["rssi"],
            n_clients=len(clients),
            clients=clients,
            first_seen=_from_iso(row["first_seen"]),
            last_seen=_from_iso(row["last_seen"]),
        )

    def get_client(self, mac: str) -> Client | None:
        cursor = self._conn.execute("SELECT * FROM clients WHERE mac = ?", (mac,))
        row = cursor.fetchone()
        if row is None:
            return None
        return Client(
            mac=row["mac"],
            bssid=row["bssid"],
            first_seen=_from_iso(row["first_seen"]),
            last_seen=_from_iso(row["last_seen"]),
        )

    def iter_observations(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Iterator[ObservationRow]:
        clauses = ["1=1"]
        params: list[str] = []
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(_iso(until))
        cursor = self._conn.execute(
            f"SELECT * FROM observations WHERE {' AND '.join(clauses)} ORDER BY id", params
        )
        for r in cursor:
            yield ObservationRow(
                timestamp=_from_iso(r["timestamp"]),
                firmware=r["firmware"],
                source=r["source"],
                event_type=r["event_type"],
                bssid=r["bssid"],
                rssi=r["rssi"],
                client_mac=r["client_mac"],
                raw_line=r["raw_line"],
            )

    def get_recent_observations(self, limit: int) -> list[ObservationRow]:
        if limit <= 0:
            return []
        cursor = self._conn.execute(
            "SELECT * FROM observations ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = cursor.fetchall()
        result: list[ObservationRow] = []
        for r in reversed(rows):
            result.append(
                ObservationRow(
                    timestamp=_from_iso(r["timestamp"]),
                    firmware=r["firmware"],
                    source=r["source"],
                    event_type=r["event_type"],
                    bssid=r["bssid"],
                    rssi=r["rssi"],
                    client_mac=r["client_mac"],
                    raw_line=r["raw_line"],
                )
            )
        return result

    def get_networks(
        self, *, since: datetime | None = None, until: datetime | None = None
    ) -> list[Network]:
        clauses = ["1=1"]
        params: list[str] = []
        if since is not None:
            clauses.append("last_seen >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("last_seen <= ?")
            params.append(_iso(until))
        cursor = self._conn.execute(
            f"SELECT * FROM networks WHERE {' AND '.join(clauses)}", params
        )
        result: list[Network] = []
        for row in cursor.fetchall():
            clients_cursor = self._conn.execute(
                "SELECT mac FROM clients WHERE bssid = ?", (row["bssid"],)
            )
            clients = {r["mac"] for r in clients_cursor.fetchall()}
            result.append(
                Network(
                    bssid=row["bssid"],
                    ssid=row["ssid"],
                    channel=row["channel"],
                    rssi=row["rssi"],
                    n_clients=len(clients),
                    clients=clients,
                    first_seen=_from_iso(row["first_seen"]),
                    last_seen=_from_iso(row["last_seen"]),
                )
            )
        return result

    def get_clients(self, *, associated_to: str | None = None) -> list[Client]:
        if associated_to is None:
            cursor = self._conn.execute("SELECT * FROM clients")
        else:
            cursor = self._conn.execute(
                "SELECT * FROM clients WHERE bssid = ?", (associated_to,)
            )
        return [
            Client(
                mac=r["mac"],
                bssid=r["bssid"],
                first_seen=_from_iso(r["first_seen"]),
                last_seen=_from_iso(r["last_seen"]),
            )
            for r in cursor.fetchall()
        ]

    def get_network_history(
        self,
        bssid: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[ObservationRow]:
        clauses = ["bssid = ?"]
        params: list[str] = [bssid]
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(_iso(until))
        cursor = self._conn.execute(
            f"SELECT * FROM observations WHERE {' AND '.join(clauses)} ORDER BY timestamp",
            params,
        )
        return [
            ObservationRow(
                timestamp=_from_iso(r["timestamp"]),
                firmware=r["firmware"],
                source=r["source"],
                event_type=r["event_type"],
                bssid=r["bssid"],
                rssi=r["rssi"],
                client_mac=r["client_mac"],
                raw_line=r["raw_line"],
            )
            for r in cursor.fetchall()
        ]

    def get_channel_distribution(self) -> dict[int, int]:
        cursor = self._conn.execute(
            "SELECT channel, COUNT(*) AS n FROM networks "
            "WHERE channel IS NOT NULL GROUP BY channel"
        )
        return {int(r["channel"]): int(r["n"]) for r in cursor.fetchall()}

    # ---- mantenimiento ----
    def prune_older_than(self, days: int, *, reference: datetime | None = None) -> int:
        ref = reference if reference is not None else utc_now()
        cutoff = _iso(ref - timedelta(days=days))
        cursor = self._conn.execute(
            "DELETE FROM observations WHERE timestamp < ?", (cutoff,)
        )
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        self._conn.close()
