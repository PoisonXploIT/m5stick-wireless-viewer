"""Fabrica de la aplicacion FastAPI (Fase 3, frontend en Fase 4).

- `create_app(store, collector=...)` es el unico punto de construccion; el
  store se inyecta y NUNCA hay instancia global mutable.
- El lifespan arranca/detiene el Collector si se proporciona: `run()` va en un
  task (una fuente serial no termina sola) y `stop()` libera la fuente al
  cerrar.
- El hub SSE (`EventHub`) se liga al event loop en el lifespan; si hay
  colector, este publica sus eventos via `hub.publish_sync` (thread-safe).
- Fase 4: `/` sirve `templates/index.html` (dashboard) y `/static` monta
  `web/static` (CSS/JS vanilla, sin frameworks).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..store.base import AbstractStore
from .api import router as api_router
from .sse import EventHub
from .sse import router as sse_router

if TYPE_CHECKING:
    from ..exporter.splunk_hec import SplunkHecExporter
    from ..worker.collector import Collector

_WEB_DIR = Path(__file__).parent
_STATIC_DIR = _WEB_DIR / "static"
_INDEX_HTML = _WEB_DIR / "templates" / "index.html"


def create_app(
    store: AbstractStore,
    *,
    collector: Collector | None = None,
    exporter: SplunkHecExporter | None = None,
) -> FastAPI:
    """Construye la app.

    - `collector` es opcional: sin el, solo lectura/export.
    - `exporter` (Splunk HEC) es opcional: si se pasa, su ciclo de vida corre
      en el lifespan y el colector le reenvia cada evento via `submit`.
    """
    hub = EventHub()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub.bind_loop(asyncio.get_running_loop())
        exporter_started = False
        if exporter is not None:
            await exporter.start()
            exporter_started = True
        task: asyncio.Task[None] | None = None
        if collector is not None:
            collector.observe(hub.publish_sync)
            if exporter is not None:
                collector.observe(exporter.submit)
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
            if exporter is not None and exporter_started:
                await exporter.stop()

    app = FastAPI(title="m5wireless", version="3.0.0", lifespan=lifespan)
    app.state.store = store
    app.state.hub = hub
    app.state.collector = collector
    app.include_router(api_router)
    app.include_router(sse_router)

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def root() -> FileResponse:
        """Dashboard (Fase 4): HTML + assets estaticos, sin build step."""
        return FileResponse(_INDEX_HTML, media_type="text/html")

    return app
