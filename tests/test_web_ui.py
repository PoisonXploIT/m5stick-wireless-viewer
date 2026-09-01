"""Tests del frontend (Fase 4): / sirve el dashboard y los assets estaticos.

El comportamiento SSE en vivo ya esta cubierto en test_sse.py; aqui solo se
verifica la capa HTML/estatico y el endpoint de consola que alimenta el
panel de lineas raw.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from m5wireless.store import MemoryStore
from m5wireless.web import create_app


@pytest.fixture
def app(seeded_store: MemoryStore) -> FastAPI:
    return create_app(seeded_store)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_root_serves_dashboard_html(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    body = res.text
    # Estructura minima del dashboard.
    assert 'id="networks-table"' in body
    assert 'id="console"' in body
    assert 'href="/static/css/style.css"' in body
    assert 'src="/static/js/dashboard.js"' in body


def test_root_serves_dashboard_with_empty_store() -> None:
    app = create_app(MemoryStore())
    with TestClient(app) as client:
        res = client.get("/")
    assert res.status_code == 200
    assert 'id="networks-table"' in res.text


def test_static_assets_served(client: TestClient) -> None:
    css = client.get("/static/css/style.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert "rssi-good" in css.text

    js = client.get("/static/js/dashboard.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]
    assert "/api/events" in js.text


def test_console_endpoint_returns_recent_raw_lines(client: TestClient) -> None:
    res = client.get("/api/console", params={"limit": 3})
    assert res.status_code == 200
    lines = res.json()["lines"]
    # Las 3 ultimas en orden de insercion (la semilla no es cronologica);
    # cada linea trae raw_line.
    assert [line["raw_line"] for line in lines] == ["de line", "c1 line", "c2 line"]
    assert all(line["timestamp"] and line["event_type"] for line in lines)


def test_console_endpoint_limit_bounds(client: TestClient) -> None:
    # Limit mayor que el total devuelve todo el historico (6 en la semilla).
    res = client.get("/api/console", params={"limit": 100})
    assert len(res.json()["lines"]) == 6
    # limit < 1 -> 422 (Query ge=1).
    assert client.get("/api/console", params={"limit": 0}).status_code == 422


# Nota: el orden/limit de `get_recent_observations` en SQLiteStore esta cubierto
# por test_store.py (parametrizado memoria/SQLite); no se repite aqui porque la
# conexion SQLite no puede cruzar el hilo del portal de TestClient.
