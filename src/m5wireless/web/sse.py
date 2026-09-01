"""Server-Sent Events (Fase 3).

`EventHub` es el pub-sub en vivo (opcion B del plan): cada cliente SSE se
suscribe a un `asyncio.Queue` propio y quien produce eventos publica. El
colector alimenta el hub a traves de `Collector.observe(hub.publish_sync)`;
los tests publican directamente, sin hardware ni fuente.

`publish_sync()` es la entrada thread-safe: el callback del colector puede
llegar desde el hilo lector serial, y `asyncio.Queue` NO es thread-safe, asi
que fuera del hilo del event loop la entrega se agenda con
`call_soon_threadsafe`.

Reconexion limpia: al cerrar el cliente, Starlette cancela/aclose() el
generador; el `finally` hace `unsubscribe` y no queda cola huérfana.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..models import ObservationEvent
from .schemas import event_to_json

router = APIRouter()

# Intervalo de keep-alive del stream SSE (comentario `: ...` por protocolo).
KEEPALIVE_SECONDS = 15.0


class EventHub:
    """Pub-sub de eventos en vivo para los clientes SSE."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[ObservationEvent]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Asocia el event loop; llamar desde su propio hilo (lifespan)."""
        self._loop = loop
        self._loop_thread = threading.get_ident()

    def subscribe(self) -> asyncio.Queue[ObservationEvent]:
        queue: asyncio.Queue[ObservationEvent] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[ObservationEvent]) -> None:
        self._subscribers.discard(queue)

    def publish_sync(self, event: ObservationEvent) -> None:
        """Publica un evento; seguro desde cualquier hilo.

        - Sin loop ligado, o desde el propio hilo del loop: entrega directa.
        - Desde otro hilo (p. ej. lector serial): `call_soon_threadsafe`.
        """
        loop = self._loop
        if loop is not None and threading.get_ident() != self._loop_thread:
            loop.call_soon_threadsafe(self._deliver, event)
        else:
            self._deliver(event)

    def _deliver(self, event: ObservationEvent) -> None:
        for queue in list(self._subscribers):
            queue.put_nowait(event)


@router.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    """Stream SSE de `ObservationEvent` serializados como JSON.

    Formato por evento: `data: {json}\n\n`. Entre eventos, un keep-alive cada
    `KEEPALIVE_SECONDS` para mantener la conexion viva en proxies.
    """
    hub: EventHub = request.app.state.hub

    async def stream() -> AsyncIterator[str]:
        queue = hub.subscribe()
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                payload = json.dumps(event_to_json(event), ensure_ascii=False)
                yield f"data: {payload}\n\n"
        finally:
            hub.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream")
