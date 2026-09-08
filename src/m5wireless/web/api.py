"""Endpoints REST (Fase 3).

El store llega por inyeccion de dependencias (`get_store`), nunca como
instancia global mutable: en produccion lo pone `create_app`, en tests se
sobreescribe con `app.dependency_overrides[get_store]`.

Semantica de filtros:
- `since` / `until`: ventana sobre `last_seen` del estado actual (ISO-8601,
  aware). Para el filtro `firmware` se usa la misma ventana sobre el
  historico.
- `min_rssi`: RSSI >= valor (las redes sin RSSI se excluyen).
"""

from __future__ import annotations

import csv
import io
import os
import string
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..models import normalize_mac
from ..store.base import AbstractStore, ObservationRow
from ..worker.collector import Collector
from .schemas import (
    ChannelDistributionResponse,
    ClientRead,
    CollectorStats,
    ConsoleResponse,
    FsBrowseResponse,
    FsEntry,
    HealthResponse,
    HistoryRow,
    ImportRequest,
    ImportResponse,
    NetworkDetail,
    NetworkListResponse,
    StatusResponse,
    client_read,
    console_line,
    history_row,
    network_detail,
    network_read,
)

router = APIRouter()

CSV_COLUMNS = (
    "timestamp",
    "firmware",
    "source",
    "event_type",
    "bssid",
    "rssi",
    "client_mac",
    "raw_line",
)


def get_store(request: Request) -> AbstractStore:
    """Dependencia FastAPI: el store inyectado al crear la app."""
    return cast(AbstractStore, request.app.state.store)


def _normalize_or_422(value: str, label: str) -> str:
    try:
        return normalize_mac(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{label} no valida: {value!r}") from None


# ---- salud ----


@router.get("/api/health", response_model=HealthResponse)
def health(request: Request, store: AbstractStore = Depends(get_store)) -> HealthResponse:
    collector = getattr(request.app.state, "collector", None)
    stats: CollectorStats | None = None
    source: str | None = None
    if isinstance(collector, Collector):
        stats = CollectorStats(**collector.stats())
        source = collector.source_type
    return HealthResponse(
        status="ok",
        store=type(store).__name__,
        source=source,
        collector=stats,
        networks=len(store.get_networks()),
        clients=len(store.get_clients()),
    )


# ---- estado de conexion ----


@router.get("/api/status", response_model=StatusResponse)
def status(request: Request) -> StatusResponse:
    """Estado de la fuente (puerto, baudrate, firmware, conectado/reconectando)."""
    collector = getattr(request.app.state, "collector", None)
    if not isinstance(collector, Collector):
        return StatusResponse(
            source=None, state=None, port=None, baudrate=None, path=None, firmware=None
        )
    info = collector.status()
    baudrate = info.get("baudrate")
    return StatusResponse(
        source=collector.source_type,
        state=str(info["state"]),
        port=cast("str | None", info.get("port")),
        baudrate=int(baudrate) if isinstance(baudrate, int) else None,
        path=cast("str | None", info.get("path")),
        firmware=str(info["firmware"]),
    )


# ---- importador de capturas (pcaps grabados / SD montada en el PC) ----
# El servidor por defecto escucha en 0.0.0.0 (acceso movil por LAN), asi que
# el navegador de ficheros y la importacion quedan restringidos a clientes
# loopback: el filesystem del anfitrion no se expone a la red.
# "testclient" es el host que inyecta TestClient de starlette en los tests.

_LOCAL_CLIENTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})
_IMPORT_EXTENSIONS = (".pcap", ".cap")
_IMPORT_MAX_BYTES = 64 * 1024 * 1024  # defensa: un pcap gigante tumbaria la memoria
_BROWSE_MAX_ENTRIES = 1000


def _require_local(request: Request) -> None:
    host = request.client.host if request.client is not None else ""
    if host not in _LOCAL_CLIENTS:
        raise HTTPException(status_code=403, detail="solo accesible desde localhost")


def _list_root_entries() -> list[FsEntry]:
    """Raiz del navegador: unidades de disco en Windows, / en POSIX."""
    if os.name == "nt":
        return [
            FsEntry(name=f"{letter}:\\", path=f"{letter}:/", kind="dir")
            for letter in string.ascii_uppercase
            if Path(f"{letter}:/").exists()
        ]
    return [FsEntry(name="/", path="/", kind="dir")]


@router.get("/api/fs/browse", response_model=FsBrowseResponse)
def browse_fs(request: Request, path: str | None = Query(None)) -> FsBrowseResponse:
    """Lista un directorio (dirs + pcaps/caps) para el modal importador."""
    _require_local(request)
    if not path:
        return FsBrowseResponse(path=None, parent=None, entries=_list_root_entries())
    root = Path(path)
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"directorio no existe: {path}")
    entries: list[FsEntry] = []
    try:
        children = sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for child in children:
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    entries.append(FsEntry(name=child.name, path=str(child), kind="dir"))
                elif child.name.lower().endswith(_IMPORT_EXTENSIONS):
                    entries.append(
                        FsEntry(
                            name=child.name,
                            path=str(child),
                            kind="file",
                            size=child.stat().st_size,
                        )
                    )
            except OSError:
                continue  # entrada esvanecida (tarjeta extraida a mitad de scan)
            if len(entries) >= _BROWSE_MAX_ENTRIES:
                break
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"no se pudo leer {path}: {exc}") from exc
    parent = str(root.parent) if root.parent != root else None
    return FsBrowseResponse(path=str(root), parent=parent, entries=entries)


@router.post("/api/import", response_model=ImportResponse)
def import_captures(request: Request, body: ImportRequest) -> ImportResponse:
    """Parsea un pcap o una carpeta de pcaps y los vuelca al pipeline en vivo.

    `def` (no `async`): FastAPI lo ejecuta en el threadpool, y el parseo de
    pcaps es CPU-bound. Los eventos entran por `collector.submit_events`, asi
    que llegan a store + SSE igual que los de una fuente binaria.
    """
    _require_local(request)
    collector = getattr(request.app.state, "collector", None)
    if not isinstance(collector, Collector):
        raise HTTPException(
            status_code=409, detail="import requiere el servidor con collector en marcha"
        )
    target = Path(body.path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"ruta no existe: {body.path}")

    from ..parser.pcap import PcapParseError, PcapParser

    parser = PcapParser()
    counts = {"files": 0, "events": 0, "errors": 0}
    messages: list[str] = []

    def ingest(path: Path) -> None:
        try:
            if path.stat().st_size > _IMPORT_MAX_BYTES:
                counts["errors"] += 1
                messages.append(f"{path.name}: supera 64 MiB, omitido")
                return
            events = parser.parse(path.read_bytes(), source="sdcard")
        except PcapParseError as exc:
            counts["errors"] += 1
            messages.append(f"{path.name}: {exc}")
            return
        except OSError as exc:
            counts["errors"] += 1
            messages.append(f"{path.name}: no legible ({exc})")
            return
        counts["files"] += 1
        counts["events"] += len(events)
        collector.submit_events(events)

    if target.is_dir():
        for path in sorted(target.rglob("*")):
            if path.is_file() and path.name.lower().endswith(_IMPORT_EXTENSIONS):
                ingest(path)
    elif target.name.lower().endswith(_IMPORT_EXTENSIONS):
        ingest(target)
    else:
        raise HTTPException(status_code=422, detail="extension no soportada (solo .pcap/.cap)")
    return ImportResponse(
        files=counts["files"],
        events=counts["events"],
        errors=counts["errors"],
        messages=messages[:20],
    )


# ---- redes ----


@router.get("/api/networks", response_model=NetworkListResponse)
def list_networks(
    store: AbstractStore = Depends(get_store),
    since: datetime | None = Query(None, description="last_seen >= since"),
    until: datetime | None = Query(None, description="last_seen <= until"),
    min_rssi: int | None = Query(None, description="RSSI >= min_rssi"),
    channel: int | None = Query(None),
    ssid: str | None = Query(None),
    firmware: str | None = Query(
        None, description="solo redes observadas por este firmware (historico)"
    ),
) -> NetworkListResponse:
    networks = store.get_networks(since=since, until=until)
    if min_rssi is not None:
        networks = [n for n in networks if n.rssi is not None and n.rssi >= min_rssi]
    if channel is not None:
        networks = [n for n in networks if n.channel == channel]
    if ssid is not None:
        networks = [n for n in networks if n.ssid == ssid]
    if firmware is not None:
        seen_bssids = {
            row.bssid
            for row in store.iter_observations(since=since, until=until)
            if row.firmware == firmware and row.bssid is not None
        }
        networks = [n for n in networks if n.bssid in seen_bssids]
    return NetworkListResponse(networks=[network_read(n) for n in networks])


@router.get("/api/networks/{bssid}", response_model=NetworkDetail)
def get_network(bssid: str, store: AbstractStore = Depends(get_store)) -> NetworkDetail:
    bssid = _normalize_or_422(bssid, "BSSID")
    net = store.get_network(bssid)
    if net is None:
        raise HTTPException(status_code=404, detail=f"red desconocida: {bssid}")
    clients = store.get_clients(associated_to=bssid)
    history = list(store.get_network_history(bssid))
    return network_detail(net, clients, history)


# ---- clientes ----


@router.get("/api/clients", response_model=list[ClientRead])
def list_clients(
    store: AbstractStore = Depends(get_store),
    bssid: str | None = Query(None, description="solo clientes de esta red"),
) -> list[ClientRead]:
    associated_to = _normalize_or_422(bssid, "BSSID") if bssid is not None else None
    return [client_read(c) for c in store.get_clients(associated_to=associated_to)]


@router.get("/api/clients/{mac}", response_model=ClientRead)
def get_client(mac: str, store: AbstractStore = Depends(get_store)) -> ClientRead:
    mac = _normalize_or_422(mac, "MAC")
    client = store.get_client(mac)
    if client is None:
        raise HTTPException(status_code=404, detail=f"cliente desconocido: {mac}")
    return client_read(client)


# ---- export ----


def _iter_csv_chunks(rows: Iterator[ObservationRow], batch_size: int = 500) -> Iterator[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> str:
        chunk = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()
        return chunk

    writer.writerow(CSV_COLUMNS)
    yield flush()
    pending = 0
    for row in rows:
        writer.writerow(
            (
                row.timestamp.isoformat(),
                row.firmware,
                row.source,
                row.event_type,
                row.bssid or "",
                "" if row.rssi is None else str(row.rssi),
                row.client_mac or "",
                row.raw_line,
            )
        )
        pending += 1
        if pending >= batch_size:
            yield flush()
            pending = 0
    tail = flush()
    if tail:
        yield tail


@router.get("/api/export/csv")
def export_csv(
    store: AbstractStore = Depends(get_store),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
) -> StreamingResponse:
    """Historico completo en CSV, streaming por lotes (no carga todo en memoria)."""
    return StreamingResponse(
        _iter_csv_chunks(store.iter_observations(since=since, until=until)),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="observations.csv"'},
    )


@router.get("/api/export/json", response_model=list[HistoryRow])
def export_json(
    store: AbstractStore = Depends(get_store),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
) -> list[HistoryRow]:
    return [history_row(r) for r in store.iter_observations(since=since, until=until)]


# ---- consola ----


@router.get("/api/console", response_model=ConsoleResponse)
def console(
    store: AbstractStore = Depends(get_store),
    limit: int = Query(100, ge=1, le=1000, description="ultimas N lineas"),
) -> ConsoleResponse:
    """Ultimas N lineas del historico (con `raw_line`), de la mas antigua a la
    mas reciente. Alimenta el panel de consola serial del dashboard."""
    return ConsoleResponse(lines=[console_line(r) for r in store.get_recent_observations(limit)])


# ---- stats ----


@router.get("/api/stats/channels", response_model=ChannelDistributionResponse)
def channel_distribution(
    store: AbstractStore = Depends(get_store),
) -> ChannelDistributionResponse:
    return ChannelDistributionResponse(channels=store.get_channel_distribution())
