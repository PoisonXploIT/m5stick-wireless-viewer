"""Fabrica de la aplicacion FastAPI (Fase 3).

- `create_app(store, collector=...)` es el unico punto de construccion; el
  store se inyecta y NUNCA hay instancia global mutable.
- El lifespan arranca/detiene el Collector si se proporciona: `run()` va en un
  task (una fuente serial no termina sola) y `stop()` libera la fuente al
  cerrar.
- El hub SSE (`EventHub`) se liga al event loop en el lifespan; si hay
  colector, este publica sus eventos via `hub.publish_sync` (thread-safe).

Decision de Fase 3: `/` devuelve un JSON minimo con los endpoints; el
dashboard real con SSE llega en Fase 4.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ..store.base import AbstractStore
from .api import router as api_router
from .sse import EventHub
from .sse import router as sse_router

if TYPE_CHECKING:
    from ..worker.collector import Collector

_ENDPOINTS = (
    "GET /api/health",
    "GET /api/networks?since&until&min_rssi&channel&ssid&firmware",
    "GET /api/networks/{bssid}",
    "GET /api/clients?bssid",
    "GET /api/clients/{mac}",
    "GET /api/export/csv?since&until",
    "GET /api/export/json?since&until",
    "GET /api/stats/channels",
    "GET /api/events",
)


def create_app(store: AbstractStore, *, collector: Collector | None = None) -> FastAPI:
    """Construye la app. `collector` es opcional: sin el, solo lectura/export."""
    hub = EventHub()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub.bind_loop(asyncio.get_running_loop())
        task: asyncio.Task[None] | None = None
        if collector is not None:
            collector.observe(hub.publish_sync)
            task = asyncio.create_task(collector.run())
        try:
            yield
        finally:
            if collector is not None and task is not None:
                await collector.stop()
                # `run()` termina cuando la fuente se cierra; si no, forzar.
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except TimeoutError:
                    # wait_for ya cancelo el task; reap para sin warnings.
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

    app = FastAPI(title="m5wireless", version="3.0.0a1", lifespan=lifespan)
    app.state.store = store
    app.state.hub = hub
    app.state.collector = collector
    app.include_router(api_router)
    app.include_router(sse_router)

    @app.get("/", include_in_schema=False)
    def root() -> JSONResponse:
        return JSONResponse({"status": "ok", "phase": 3, "endpoints": _ENDPOINTS})

    return app
