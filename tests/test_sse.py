"""Tests del stream SSE y del wiring collector -> hub (Fase 3).

Nota: la version de starlette en este entorno hace que TestClient espere a
que el ASGI app termine ANTES de devolver la respuesta, asi que los tests de
streaming manejan la app ASGI directamente (lifespan + request manuales) en
lugar de usar `client.stream`. El resto (health con collector) usa
TestClient normal.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from m5wireless.models import Client, ClientAssociated, Network, NetworkSeen, ObservationEvent
from m5wireless.parser import get_parser
from m5wireless.source import FileSource
from m5wireless.store import MemoryStore
from m5wireless.web import create_app
from m5wireless.web.sse import EventHub
from m5wireless.worker import Collector

# Mismo instante de referencia que tests/conftest.py (determinista).
NOW = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)


def _network_event() -> NetworkSeen:
    return NetworkSeen(
        timestamp=NOW,
        firmware="marauder",
        source="serial",
        raw_line="aa:bb:cc:dd:ee:01 Movistar_1A2B 6 -55",
        network=Network(bssid="aa:bb:cc:dd:ee:01", ssid="Movistar_1A2B", channel=6, rssi=-55),
    )


def _client_event() -> ClientAssociated:
    return ClientAssociated(
        timestamp=NOW,
        firmware="marauder",
        source="serial",
        raw_line="client line",
        client=Client(mac="ff:ee:dd:cc:bb:aa", bssid="aa:bb:cc:dd:ee:01"),
    )


def _run_sse(
    app: Any,
    *,
    publish: Callable[[EventHub], None] | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    """Arranca la app ASGI a mano y consume /api/events.

    Devuelve `(payload, saw_keepalive)`: el primer frame `data:` parseado y si
    se vio un keep-alive. Al final cancela el request (simula cierre del
    cliente) y apaga el lifespan: verifica la reconexion/desconexion limpia.
    """

    async def main() -> tuple[dict[str, Any] | None, bool]:
        hub: EventHub = app.state.hub

        startup_done = asyncio.Event()
        shutdown_sent = asyncio.Event()
        started = asyncio.Event()

        async def ls_receive() -> dict[str, str]:
            if not startup_done.is_set():
                startup_done.set()
                return {"type": "lifespan.startup"}
            await shutdown_sent.wait()
            return {"type": "lifespan.shutdown"}

        async def ls_send(message: dict[str, object]) -> None:
            if message["type"] == "lifespan.startup.complete":
                started.set()

        lifespan_task = asyncio.create_task(
            app({"type": "lifespan"}, ls_receive, ls_send)  # type: ignore[arg-type]
        )
        await asyncio.wait_for(started.wait(), timeout=5.0)

        bodies: list[bytes] = []
        request_sent = asyncio.Event()

        async def receive() -> dict[str, object]:
            if not request_sent.is_set():
                request_sent.set()
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.sleep(3600.0)  # nunca desconecta durante el test
            return {"type": "http.disconnect"}

        async def send(message: dict[str, object]) -> None:
            if message["type"] == "http.response.body":
                bodies.append(bytes(message.get("body", b"")))

        scope: dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/api/events",
            "raw_path": "/api/events",
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "state": {},
        }
        http_task = asyncio.create_task(app(scope, receive, send))  # type: ignore[arg-type]

        deadline = time.monotonic() + 5.0
        while hub.subscriber_count == 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert hub.subscriber_count == 1, "el endpoint no se suscribio al hub"

        if publish is not None:
            publish(hub)

        payload: dict[str, Any] | None = None
        saw_keepalive = False
        while time.monotonic() < deadline:
            text = b"".join(bodies).decode("utf-8")
            for line in text.splitlines():
                if line.startswith("data: ") and payload is None:
                    payload = json.loads(line[len("data: ") :])
                elif line == ": keep-alive":
                    saw_keepalive = True
            if payload is not None or saw_keepalive:
                break
            await asyncio.sleep(0.01)

        # Cierre del cliente: cancela el task; el finally del generador hace
        # unsubscribe y no debe quedar ningun suscriptor.
        http_task.cancel()
        try:
            await http_task
        except asyncio.CancelledError:
            pass
        assert hub.subscriber_count == 0, "quedo un suscriptor huorfano tras el cierre"

        shutdown_sent.set()
        try:
            await asyncio.wait_for(lifespan_task, timeout=5.0)
        except (TimeoutError, asyncio.CancelledError):
            lifespan_task.cancel()
        return payload, saw_keepalive

    return asyncio.run(main())


@pytest.fixture
def app() -> Any:
    return create_app(MemoryStore())


def test_sse_receives_events_live(app: Any) -> None:
    """Un evento publicado tras conectar llega como frame `data:` JSON."""
    payload, _ = _run_sse(app, publish=lambda hub: hub.publish_sync(_network_event()))
    assert payload is not None
    assert payload["event"] == "network_seen"
    assert payload["bssid"] == "aa:bb:cc:dd:ee:01"
    assert payload["ssid"] == "Movistar_1A2B"
    assert payload["channel"] == 6
    assert payload["rssi"] == -55
    assert payload["timestamp"].replace("Z", "+00:00") == NOW.isoformat()


def test_sse_client_associated_event(app: Any) -> None:
    payload, _ = _run_sse(app, publish=lambda hub: hub.publish_sync(_client_event()))
    assert payload is not None
    assert payload["event"] == "client_associated"
    assert payload["mac"] == "ff:ee:dd:cc:bb:aa"
    assert payload["bssid"] == "aa:bb:cc:dd:ee:01"


def test_sse_fanout_to_two_subscribers(app: Any) -> None:
    """Dos colas suscritas reciben el mismo evento (fan-out)."""

    async def main() -> tuple[list[ObservationEvent], list[ObservationEvent]]:
        hub: EventHub = app.state.hub
        q1, q2 = hub.subscribe(), hub.subscribe()
        try:
            assert hub.subscriber_count == 2
            hub.publish_sync(_network_event())
            first = await asyncio.wait_for(q1.get(), timeout=5.0)
            second = await asyncio.wait_for(q2.get(), timeout=5.0)
            return first, second
        finally:
            hub.unsubscribe(q1)
            hub.unsubscribe(q2)

    first, second = asyncio.run(main())
    assert isinstance(first, NetworkSeen) and isinstance(second, NetworkSeen)
    assert first.network.bssid == second.network.bssid == "aa:bb:cc:dd:ee:01"


def test_sse_keepalive(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin eventos, el stream emite keep-alive para mantener la conexion."""
    import m5wireless.web.sse as sse_mod

    monkeypatch.setattr(sse_mod, "KEEPALIVE_SECONDS", 0.2)
    _, saw_keepalive = _run_sse(app)
    assert saw_keepalive


def test_publish_sync_from_foreign_thread() -> None:
    """`publish_sync` desde un hilo ajeno al loop usa call_soon_threadsafe."""
    hub = EventHub()
    received: list[ObservationEvent] = []
    ready = threading.Event()

    def run_loop() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        hub.bind_loop(loop)
        queue = hub.subscribe()

        async def drain() -> None:
            for _ in range(2):
                received.append(await queue.get())
            loop.call_soon(loop.stop)

        ready.set()
        loop.run_until_complete(asyncio.ensure_future(drain()))
        hub.unsubscribe(queue)

    thread = threading.Thread(target=run_loop, daemon=True)
    thread.start()
    assert ready.wait(5.0)

    ev1, ev2 = _network_event(), _client_event()
    hub.publish_sync(ev1)
    hub.publish_sync(ev2)

    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert received == [ev1, ev2]


def test_collector_lifespan_health(marauder_log_path: Path) -> None:
    """Con collector (TestClient, no streaming): el lifespan arranca la
    pipeline y /api/health refleja fuente, stats y datos en el store."""
    store = MemoryStore()
    source = FileSource(marauder_log_path, follow=False)
    collector = Collector(source, get_parser("marauder"), store, source_type="file")
    app = create_app(store, collector=collector)

    with TestClient(app) as client:
        body: dict[str, Any] | None = None
        for _ in range(50):  # el task del colector corre en el mismo loop.
            body = client.get("/api/health").json()
            if body["collector"]["events"] >= 4:
                break
        assert body is not None
        assert body["status"] == "ok"
        assert body["source"] == "file"
        assert body["networks"] == 3
        assert body["clients"] == 0
        assert body["collector"]["errors"] == 0

    # Fuera del with, el lifespan ya hizo stop(): la fuente no sigue corriendo.
    assert not source._running


def test_collector_observe_delivers_each_event(marauder_log_path: Path) -> None:
    """`observe()` recibe cada evento tras `apply`, sin parar la pipeline."""
    store = MemoryStore()
    source = FileSource(marauder_log_path, follow=False)  # no se arranca aqui.
    collector = Collector(source, get_parser("marauder"), store, source_type="file")
    seen: list[ObservationEvent] = []
    collector.observe(seen.append)

    lines = marauder_log_path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        collector._on_line(line)  # whitebox: mismo callback que usa run().

    assert len(seen) == 4  # los 4 eventos del fixture marauder.
    assert collector.stats() == {"lines": len(lines), "events": 4, "errors": 0}
