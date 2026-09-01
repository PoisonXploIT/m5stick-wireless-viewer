"""Tests del exporter Splunk HEC (Fase 5).

Sin red real: ``httpx.MockTransport`` como transporte. Lo que se verifica
aqui es la logica robusta: cola thread-safe, circuit breaker, spool a disco y
verify por defecto.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from m5wireless.exporter.splunk_hec import SplunkHecConfig, SplunkHecExporter, event_to_payload
from m5wireless.models import Network, NetworkSeen

NOW = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)


def _event(i: int = 0) -> NetworkSeen:
    return NetworkSeen(
        timestamp=NOW,
        firmware="marauder",
        source="file",
        raw_line=f"line {i}",
        network=Network(bssid="aa:bb:cc:dd:ee:ff", ssid="TestNet", channel=6, rssi=-50),
    )


async def _wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout=timeout)


def make_exporter(
    handler: Any, *, spool_path: str | Path | None = None, batch_size: int = 2, **cfg: Any
) -> SplunkHecExporter:
    config = SplunkHecConfig(
        url="https://splunk.test/services/collector/event",
        token="tok-123",
        batch_size=batch_size,
        spool_path=spool_path,
        **cfg,
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), verify=True)
    return SplunkHecExporter(config, client=client)


def test_event_to_payload_flattens_network_seen() -> None:
    payload = event_to_payload(_event(7))
    assert payload == {
        "timestamp": NOW.isoformat(),
        "firmware": "marauder",
        "source": "file",
        "event_type": "network_seen",
        "bssid": "aa:bb:cc:dd:ee:ff",
        "rssi": -50,
        "client_mac": None,
        "raw_line": "line 7",
    }


def test_verify_is_true_by_default() -> None:
    config = SplunkHecConfig(url="https://x", token="t")
    assert config.verify is True
    # verify=False solo via configuracion explicita:
    assert SplunkHecConfig(url="https://x", token="t", verify=False).verify is False


def test_exporter_starts_and_stops_without_injected_client() -> None:
    """Con client=None crea su propio AsyncClient (sin trafico) y lo cierra."""

    async def scenario() -> None:
        exporter = SplunkHecExporter(SplunkHecConfig(url="https://x", token="t"))
        await exporter.start()
        assert exporter.verify is True
        await exporter.stop()

    asyncio.run(scenario())


def test_submits_are_sent_with_auth_and_sourcetype() -> None:
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        return httpx.Response(200)

    async def scenario() -> None:
        exporter = make_exporter(handler)
        await exporter.start()
        try:
            exporter.submit(_event(1))
            await _wait_until(lambda: exporter.stats()["sent"] == 1)
        finally:
            await exporter.stop()

    asyncio.run(scenario())
    assert len(requests_seen) == 1
    request = requests_seen[0]
    assert request.headers["authorization"] == "Splunk tok-123"
    assert request.url.params["sourcetype"] == "m5wireless"
    body = json.loads(request.content)
    assert body["raw_line"] == "line 1"
    assert body["bssid"] == "aa:bb:cc:dd:ee:ff"


def test_events_buffer_while_hec_down_and_drain_after_recovery() -> None:
    """HEC cae: la cola crece y el circuit breaker pausa envios; al
    recuperarse, lo pendiente en cola se envia. Los fallos previos se cuentan
    (no se reenvian); los que seguian en cola no se pierden."""
    requests_seen: list[httpx.Request] = []
    state = {"down": True}

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        return httpx.Response(503 if state["down"] else 200)

    async def scenario() -> None:
        exporter = make_exporter(
            handler, batch_size=10, circuit_breaker_threshold=2, circuit_breaker_pause=1.0
        )
        await exporter.start()
        try:
            for i in range(6):
                exporter.submit(_event(i))
            # 2 fallos consecutivos -> breaker abierto; el resto queda en cola.
            await _wait_until(lambda: exporter.stats()["failed"] == 2)
            assert len(requests_seen) == 2
            assert exporter.queue_size() == 4
            # Durante la pausa (1 s) no hay nuevos envios.
            await asyncio.sleep(0.3)
            assert len(requests_seen) == 2
            state["down"] = False
            await _wait_until(lambda: exporter.stats()["sent"] == 4)
        finally:
            await exporter.stop()

    asyncio.run(scenario())
    # 6 eventos = 2 fallos contados + 4 enviados al recuperarse.
    assert len(requests_seen) >= 2


def test_spool_overflow_persists_to_disk_and_reloads(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    async def scenario() -> None:
        spool = tmp_path / "spool.jsonl"
        exporter = make_exporter(handler, max_queue_size=2, spool_path=spool)
        # Sin start(): se verifica cola + spool (submit no necesita loop).
        for i in range(3):
            exporter.submit(_event(i))
        assert exporter.queue_size() == 2
        assert exporter.stats()["dropped"] == 0
        lines = spool.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["raw_line"] == "line 2"

        # Un exporter nuevo con el mismo spool lo recarga al arrancar
        # (cap mas amplia: 1 cargada + 2 nuevas caben en memoria).
        reloaded = make_exporter(handler, max_queue_size=10, spool_path=spool)
        await reloaded.start()
        try:
            assert reloaded.queue_size() == 1
            reloaded.submit(_event(3))
            reloaded.submit(_event(4))
            await _wait_until(lambda: reloaded.stats()["sent"] == 3)
        finally:
            await reloaded.stop()

    asyncio.run(scenario())


def test_overflow_without_spool_drops_and_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    exporter = make_exporter(handler, max_queue_size=1)
    exporter.submit(_event(0))
    exporter.submit(_event(1))
    assert exporter.queue_size() == 1
    assert exporter.stats()["dropped"] == 1


def test_submit_from_foreign_threads_is_thread_safe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    async def scenario() -> None:
        exporter = make_exporter(handler, batch_size=50)
        await exporter.start()
        try:
            threads = [threading.Thread(target=lambda: [exporter.submit(_event(i)) for i in range(20)]) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            await _wait_until(lambda: exporter.stats()["sent"] == 80)
        finally:
            await exporter.stop()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [500, 429])
def test_non_2xx_counts_as_failure(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async def scenario() -> None:
        exporter = make_exporter(handler, circuit_breaker_threshold=99)
        await exporter.start()
        try:
            exporter.submit(_event(0))
            await _wait_until(lambda: exporter.stats()["failed"] == 1)
        finally:
            await exporter.stop()

    asyncio.run(scenario())
