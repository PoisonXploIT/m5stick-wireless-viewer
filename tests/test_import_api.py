"""Tests del importador de capturas: /api/fs/browse + /api/import.

El builder de pcaps se reutiliza de test_pcap_parser (misma estructura que
el fixture real). No hace falta hardware: cualquier pcap en una carpeta
temporal es un caso real de importacion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

# Mismos builders sinteticos que tests/test_pcap_parser.py.
from test_pcap_parser import _build_eapol, _build_mgmt, _wrap_pcap

from m5wireless.parser.registry import get_parser
from m5wireless.source.file_source import FileSource
from m5wireless.store import MemoryStore
from m5wireless.web import create_app
from m5wireless.web.api import get_store
from m5wireless.worker.collector import Collector

AP_MAC = "aa:bb:cc:dd:ee:01"
CLIENT_MAC = "0a:5e:1d:a6:e0:51"
TS = (1_700_000_000, 0)


def _valid_pcap(ssid: str) -> bytes:
    return _wrap_pcap(
        [
            _build_mgmt(ssid=ssid, ts=TS),
            _build_eapol(ts=TS),
        ]
    )


@pytest.fixture
def client(seeded_store: MemoryStore) -> TestClient:
    """App sin collector (mismo patron que tests/test_api.py)."""
    app = create_app(seeded_store)
    app.dependency_overrides[get_store] = lambda: seeded_store
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_with_collector(seeded_store: MemoryStore, marauder_log_path: Path) -> TestClient:
    """App con collector (no arrancado: submit_events funciona igual)."""
    source = FileSource(str(marauder_log_path), follow=False)
    collector = Collector(source, get_parser("marauder"), seeded_store, source_type="file")
    app = create_app(seeded_store, collector=collector)
    app.dependency_overrides[get_store] = lambda: seeded_store
    with TestClient(app) as test_client:
        yield test_client


# ---- browse ----


def test_browse_root(client: TestClient) -> None:
    res = client.get("/api/fs/browse")
    assert res.status_code == 200
    body = res.json()
    assert body["path"] is None
    assert body["parent"] is None
    assert len(body["entries"]) >= 1
    assert all(e["kind"] == "dir" for e in body["entries"])


def test_browse_dir_lists_dirs_and_pcaps(client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "handshakes").mkdir()
    (tmp_path / "captura.pcap").write_bytes(_valid_pcap("TESTNET"))
    (tmp_path / ".oculto").mkdir()
    (tmp_path / "notas.txt").write_text("hola", encoding="utf-8")
    res = client.get("/api/fs/browse", params={"path": str(tmp_path)})
    assert res.status_code == 200
    body = res.json()
    assert body["path"] == str(tmp_path)
    assert body["parent"] == str(tmp_path.parent)
    by_name = {e["name"]: e for e in body["entries"]}
    assert by_name["handshakes"]["kind"] == "dir"
    assert by_name["captura.pcap"]["kind"] == "file"
    assert by_name["captura.pcap"]["size"] == (tmp_path / "captura.pcap").stat().st_size
    assert ".oculto" not in by_name
    assert "notas.txt" not in by_name


def test_browse_missing_dir(client: TestClient) -> None:
    res = client.get("/api/fs/browse", params={"path": "Z:/no/existe/nada"})
    assert res.status_code == 404


# ---- import ----


def test_import_file(client_with_collector: TestClient, tmp_path: Path) -> None:
    pcap = tmp_path / "importada.pcap"
    pcap.write_bytes(_valid_pcap("RED_IMPORTADA"))
    res = client_with_collector.post("/api/import", json={"path": str(pcap)})
    assert res.status_code == 200
    body = res.json()
    assert body["files"] == 1
    # 1 NetworkSeen + 1 ClientAssociated.
    assert body["events"] == 2
    assert body["errors"] == 0

    nets = client_with_collector.get("/api/networks").json()["networks"]
    assert any(n["ssid"] == "RED_IMPORTADA" and n["bssid"] == AP_MAC for n in nets)
    clients = client_with_collector.get("/api/clients").json()
    assert any(c["mac"] == CLIENT_MAC for c in clients)


def test_import_dir_counts_errors(client_with_collector: TestClient, tmp_path: Path) -> None:
    sub = tmp_path / "sd"
    sub.mkdir()
    (sub / "bien.pcap").write_bytes(_valid_pcap("OK_NET"))
    (sub / "roto.pcap").write_bytes(b"no es un pcap")
    res = client_with_collector.post("/api/import", json={"path": str(sub)})
    assert res.status_code == 200
    body = res.json()
    assert body["files"] == 1
    assert body["events"] == 2
    assert body["errors"] == 1
    assert len(body["messages"]) == 1
    assert "roto.pcap" in body["messages"][0]


def test_import_unsupported_extension(client_with_collector: TestClient, tmp_path: Path) -> None:
    txt = tmp_path / "notas.txt"
    txt.write_text("hola", encoding="utf-8")
    res = client_with_collector.post("/api/import", json={"path": str(txt)})
    assert res.status_code == 422


def test_import_missing_path(client_with_collector: TestClient) -> None:
    res = client_with_collector.post("/api/import", json={"path": "Z:/no/existe.pcap"})
    assert res.status_code == 404


def test_import_without_collector(client: TestClient, tmp_path: Path) -> None:
    pcap = tmp_path / "x.pcap"
    pcap.write_bytes(_valid_pcap("NADA"))
    res = client.post("/api/import", json={"path": str(pcap)})
    assert res.status_code == 409


# ---- gate loopback ----


def test_fs_endpoints_require_loopback(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("m5wireless.web.api._LOCAL_CLIENTS", frozenset())
    assert client.get("/api/fs/browse").status_code == 403
    res = client.post("/api/import", json={"path": "C:/x.pcap"})
    assert res.status_code == 403
