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
_NETWORK_HTML = _WEB_DIR / "templates" / "network.html"

# Rutas cuyo contenido cambia entre versiones del paquete: sin `Cache-Control`
# el navegador aplica heuristic caching y puede servir CSS/JS viejo tras
# actualizar (el usuario "no ve cambios"). `no-cache` = revalidar siempre;
# el ETag/Last-Modified del StaticFiles hace la revalidacion barata (304).
_NO_CACHE_PATHS = ("/", "/network")
_NO_CACHE_PREFIXES = ("/static/",)


class _NoCacheMiddleware:
    """ASGI puro (sin buffering): anade `Cache-Control: no-cache` a los assets.

    BaseHTTPMiddleware no sirve aqui: bufferiza respuestas streaming y
    romperia el SSE de /api/events. Este middleware solo toca las cabeceras
    del http.response.start de las rutas versionables.
    """

    def __init__(self, app: object) -> None:
        self._app = app

    def _applies(self, path: str) -> bool:
        return path in _NO_CACHE_PATHS or any(path.startswith(p) for p in _NO_CACHE_PREFIXES)

    async def __call__(self, scope: object, receive: object, send: object) -> None:
        if not isinstance(scope, dict) or scope.get("type") != "http":
            await self._app(scope, receive, send)  # type: ignore[operator]
            return
        path = str(scope.get("path", ""))
        if not self._applies(path):
            await self._app(scope, receive, send)  # type: ignore[operator]
            return

        async def send_no_cache(message: object) -> None:
            if isinstance(message, dict) and message.get("type") == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"cache-control", b"no-cache"))
            await send(message)  # type: ignore[operator]

        await self._app(scope, receive, send_no_cache)  # type: ignore[operator]


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

    from .. import __version__

    app = FastAPI(title="m5wireless", version=__version__, lifespan=lifespan)
    app.add_middleware(_NoCacheMiddleware)
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

    @app.get("/network", include_in_schema=False)
    def network() -> FileResponse:
        """Vista de detalle de red (clientes, historico, evolucion RSSI)."""
        return FileResponse(_NETWORK_HTML, media_type="text/html")

    return app
