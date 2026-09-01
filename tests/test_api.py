"""Tests de la API REST con TestClient + MemoryStore (Fase 3)."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from m5wireless.store import MemoryStore
from m5wireless.web import create_app
from m5wireless.web.api import get_store

# Mismo instante de referencia que tests/conftest.py (determinista).
NOW = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _ts(value: str) -> datetime:
    """Parsea un timestamp de la respuesta (pydantic emite UTC con sufijo Z)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture
def client(seeded_store: MemoryStore) -> TestClient:
    """App con el store inyectado por dependencia (override, sin estado global)."""
    app = create_app(seeded_store)
    app.dependency_overrides[get_store] = lambda: seeded_store
    with TestClient(app) as test_client:
        yield test_client


# ---- / ----


def test_root(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["phase"] == 3
    assert "GET /api/events" in body["endpoints"]


# ---- health ----


def test_health(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["store"] == "MemoryStore"
    assert body["source"] is None
    assert body["collector"] is None
    assert body["networks"] == 3
    assert body["clients"] == 2


# ---- networks ----


def test_list_networks(client: TestClient) -> None:
    res = client.get("/api/networks")
    assert res.status_code == 200
    body = res.json()
    bssids = {n["bssid"] for n in body["networks"]}
    assert bssids == {"aa:bb:cc:dd:ee:01", "11:22:33:44:55:66", "de:ad:be:ef:00:99"}

    aa = next(n for n in body["networks"] if n["bssid"] == "aa:bb:cc:dd:ee:01")
    assert aa["rssi"] == -70  # ultimo valor, no el primero.
    assert aa["n_clients"] == 1
    assert aa["client_macs"] == ["ff:ee:dd:cc:bb:aa"]
    assert _ts(aa["first_seen"]) == NOW - timedelta(hours=2)
    assert _ts(aa["last_seen"]) == NOW


def test_list_networks_filters(client: TestClient) -> None:
    # min_rssi excluye a 11..66 (-80).
    res = client.get("/api/networks", params={"min_rssi": -75})
    assert {n["bssid"] for n in res.json()["networks"]} == {
        "aa:bb:cc:dd:ee:01",
        "de:ad:be:ef:00:99",
    }

    # channel exacto.
    res = client.get("/api/networks", params={"channel": 6})
    assert [n["bssid"] for n in res.json()["networks"]] == ["aa:bb:cc:dd:ee:01"]

    # ssid exacto.
    res = client.get("/api/networks", params={"ssid": "CafeWiFi"})
    assert [n["bssid"] for n in res.json()["networks"]] == ["de:ad:be:ef:00:99"]

    # since sobre last_seen: 11..66 (last_seen = NOW-1h) queda fuera.
    since = (NOW - timedelta(minutes=30)).isoformat()
    res = client.get("/api/networks", params={"since": since})
    assert {n["bssid"] for n in res.json()["networks"]} == {
        "aa:bb:cc:dd:ee:01",
        "de:ad:be:ef:00:99",
    }

    # firmware: solo redes observadas por ese firmware en el historico.
    res = client.get("/api/networks", params={"firmware": "evil_m5project"})
    assert [n["bssid"] for n in res.json()["networks"]] == ["de:ad:be:ef:00:99"]


def test_get_network_detail(client: TestClient) -> None:
    res = client.get("/api/networks/aa:bb:cc:dd:ee:01")
    assert res.status_code == 200
    body = res.json()
    assert body["bssid"] == "aa:bb:cc:dd:ee:01"
    assert body["ssid"] == "Movistar_1A2B"
    assert body["channel"] == 6
    assert body["rssi"] == -70
    assert body["n_clients"] == 1
    assert [c["mac"] for c in body["clients"]] == ["ff:ee:dd:cc:bb:aa"]
    # Historico: 2 lineas de red + 1 linea de cliente asociado.
    assert len(body["history"]) == 3
    event_types = {h["event_type"] for h in body["history"]}
    assert event_types == {"network_seen", "client_associated"}


def test_get_network_normalizes_bssid(client: TestClient) -> None:
    res = client.get("/api/networks/AA-BB-CC-DD-EE-01")
    assert res.status_code == 200
    assert res.json()["bssid"] == "aa:bb:cc:dd:ee:01"


def test_get_network_unknown_404(client: TestClient) -> None:
    res = client.get("/api/networks/de:ad:be:ef:ca:fe")
    assert res.status_code == 404


def test_get_network_invalid_bssid_422(client: TestClient) -> None:
    res = client.get("/api/networks/no-una-bssid")
    assert res.status_code == 422


# ---- clients ----


def test_list_clients(client: TestClient) -> None:
    res = client.get("/api/clients")
    assert res.status_code == 200
    body = res.json()
    assert {c["mac"] for c in body} == {"ff:ee:dd:cc:bb:aa", "00:11:22:33:44:55"}

    # Filtro por red.
    res = client.get("/api/clients", params={"bssid": "de:ad:be:ef:00:99"})
    assert [c["mac"] for c in res.json()] == ["00:11:22:33:44:55"]


def test_get_client_detail(client: TestClient) -> None:
    res = client.get("/api/clients/ff:ee:dd:cc:bb:aa")
    assert res.status_code == 200
    body = res.json()
    assert body["mac"] == "ff:ee:dd:cc:bb:aa"
    assert body["bssid"] == "aa:bb:cc:dd:ee:01"
    assert _ts(body["first_seen"]) == NOW - timedelta(hours=2)
    assert _ts(body["last_seen"]) == NOW - timedelta(hours=2)


def test_get_client_unknown_404(client: TestClient) -> None:
    res = client.get("/api/clients/de:ad:be:ef:ca:fe")
    assert res.status_code == 404


def test_get_client_invalid_mac_422(client: TestClient) -> None:
    res = client.get("/api/clients/no-una-mac")
    assert res.status_code == 422


# ---- export ----


def _csv_rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_export_csv(client: TestClient) -> None:
    res = client.get("/api/export/csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    rows = _csv_rows(res.text)
    header, *data = rows
    assert header == [
        "timestamp",
        "firmware",
        "source",
        "event_type",
        "bssid",
        "rssi",
        "client_mac",
        "raw_line",
    ]
    # 6 observaciones en la semilla.
    assert len(data) == 6
    event_types = {row[3] for row in data}
    assert event_types == {"network_seen", "client_associated"}


def test_export_csv_since_filter(client: TestClient) -> None:
    since = (NOW - timedelta(minutes=30)).isoformat()
    res = client.get("/api/export/csv", params={"since": since})
    rows = _csv_rows(res.text)
    # Solo observaciones con timestamp >= NOW-30m: 2 lineas de red + 1 cliente.
    assert len(rows) == 4


def test_export_json(client: TestClient) -> None:
    res = client.get("/api/export/json")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    assert len(body) == 6
    first = body[0]
    assert _ts(first["timestamp"]) == NOW - timedelta(hours=2)
    assert first["firmware"] == "marauder"
    assert first["event_type"] == "network_seen"


# ---- stats ----


def test_channel_distribution(client: TestClient) -> None:
    res = client.get("/api/stats/channels")
    assert res.status_code == 200
    # Claves de objeto JSON son strings.
    assert res.json() == {"channels": {"1": 1, "6": 1, "11": 1}}
