"""Exporter a Splunk HEC (HTTP Event Collector), robusto (Fase 5).

Diseño:

- ``httpx`` async para el envío; ``verify=True`` por defecto. Desactivar la
  verificación TLS es una decisión explícita de configuración, nunca un
  default.
- Los eventos se bufferizan en una cola en memoria **thread-safe**: el
  colector llama a :meth:`SplunkHecExporter.submit` desde el hilo que
  procesa las líneas (serial o file), sin tocar asyncio directamente.
- Si HEC cae, los eventos quedan en la cola y el envío se reanuda solo; si la
  cola supera ``max_queue_size`` y hay ``spool_path``, el desbordamiento se
  persiste a disco como JSONL (se recarga al arrancar). Sin spool, el exceso
  se descarta y se cuenta en ``stats()["dropped"]``.
- Circuit breaker: tras ``circuit_breaker_threshold`` fallos consecutivos,
  los envíos se pausan ``circuit_breaker_pause`` segundos; la cola no se
  pierde. Un envío exitoso cierra el breaker.

El ciclo de vida (``start``/``stop``) lo gestiona quien orquesta: la CLI lo
hace vía el lifespan de FastAPI en ``create_app(..., exporter=...)``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..models import ObservationEvent
from ..store.base import event_to_observation_row

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SplunkHecConfig:
    """Configuración del exporter.

    - ``url``: endpoint HEC completo, p. ej.
      ``https://splunk:8088/services/collector/event``.
    - ``verify``: verificación de certificados TLS. Default ``True``; solo se
      desactiva con configuración explícita (CLI/env/toml).
    """

    url: str
    token: str
    batch_size: int = 100
    max_queue_size: int = 10_000
    spool_path: str | Path | None = None
    circuit_breaker_threshold: int = 5
    circuit_breaker_pause: float = 30.0
    verify: bool = True
    timeout: float = 10.0
    sourcetype: str = "m5wireless"


def event_to_payload(event: ObservationEvent) -> dict[str, Any]:
    """Convierte un ``ObservationEvent`` en el payload JSON que se envía a HEC."""
    row = event_to_observation_row(event)
    return {
        "timestamp": row.timestamp.isoformat(),
        "firmware": row.firmware,
        "source": row.source,
        "event_type": row.event_type,
        "bssid": row.bssid,
        "rssi": row.rssi,
        "client_mac": row.client_mac,
        "raw_line": row.raw_line,
    }


class SplunkHecExporter:
    """Reenvía ``ObservationEvent`` a Splunk HEC con cola y circuit breaker."""

    def __init__(
        self,
        config: SplunkHecConfig,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._client = client
        self._clock = clock
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._task: asyncio.Task[None] | None = None
        self._consecutive_failures = 0
        self._open_until = 0.0
        self._stats = {"sent": 0, "failed": 0, "dropped": 0}

    # ---- API pública ----
    @property
    def verify(self) -> bool:
        """Verificación TLS configurada (``True`` por defecto)."""
        return self._config.verify

    def queue_size(self) -> int:
        with self._lock:
            return len(self._queue)

    def stats(self) -> dict[str, int]:
        return {**self._stats, "queued": self.queue_size()}

    def submit(self, event: ObservationEvent) -> None:
        """Encola un evento. Thread-safe y nunca bloquea ni lanza."""
        payload = event_to_payload(event)
        with self._lock:
            if len(self._queue) >= self._config.max_queue_size:
                spool = self._config.spool_path
                if spool is not None:
                    with Path(spool).open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    logger.debug("cola HEC llena: evento a spool %s", spool)
                else:
                    self._stats["dropped"] += 1
                    logger.warning(
                        "cola HEC llena sin spool: evento descartado (dropped=%d)",
                        self._stats["dropped"],
                    )
            else:
                self._queue.append(payload)

    async def start(self) -> None:
        """Crea el cliente (si no se inyectó), carga el spool y arranca el drain."""
        if self._client is None:
            cfg = self._config
            self._client = httpx.AsyncClient(verify=cfg.verify, timeout=cfg.timeout)
        self._load_spool()
        self._task = asyncio.create_task(self._drain_loop())

    async def stop(self, drain_timeout: float = 5.0) -> None:
        """Drena la cola (best effort), cancela el drain y cierra el cliente."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        deadline = self._clock() + drain_timeout
        while True:
            batch = self._take_batch(self._config.batch_size)
            if not batch:
                break
            await self._send_batch(batch)
            if self._clock() >= deadline:
                logger.warning("stop HEC: drenado incompleto, quedan %d", self.queue_size())
                break
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    # ---- interno ----
    def _take_batch(self, size: int) -> list[dict[str, Any]]:
        batch: list[dict[str, Any]] = []
        with self._lock:
            while len(batch) < size and self._queue:
                batch.append(self._queue.popleft())
        return batch

    def _requeue_front(self, remaining: list[dict[str, Any]]) -> None:
        with self._lock:
            for payload in reversed(remaining):
                self._queue.appendleft(payload)

    def _breaker_open(self) -> bool:
        return self._clock() < self._open_until

    async def _drain_loop(self) -> None:
        while True:
            if self._breaker_open():
                await asyncio.sleep(0.1)
                continue
            batch = self._take_batch(self._config.batch_size)
            if not batch:
                await asyncio.sleep(0.05)
                continue
            await self._send_batch(batch)

    async def _send_batch(self, batch: list[dict[str, Any]]) -> None:
        for i, payload in enumerate(batch):
            ok = await self._post(payload)
            if ok:
                self._stats["sent"] += 1
                self._consecutive_failures = 0
            else:
                self._stats["failed"] += 1
                self._consecutive_failures += 1
                if (
                    self._consecutive_failures >= self._config.circuit_breaker_threshold
                    and not self._breaker_open()
                ):
                    self._open_until = self._clock() + self._config.circuit_breaker_pause
                    logger.warning(
                        "circuit breaker HEC abierto: %d fallos consecutivos, pausa %.0f s",
                        self._consecutive_failures,
                        self._config.circuit_breaker_pause,
                    )
            if self._breaker_open():
                # El resto del lote vuelve a la cola (al frente) y se reintentan
                # cuando el breaker se cierre.
                self._requeue_front(batch[i + 1 :])
                break

    async def _post(self, payload: dict[str, Any]) -> bool:
        client = self._client
        if client is None:
            return False
        try:
            resp = await client.post(
                self._config.url,
                json=payload,
                params={"sourcetype": self._config.sourcetype},
                headers={"Authorization": f"Splunk {self._config.token}"},
            )
        except httpx.HTTPError as exc:
            logger.warning("HEC inalcanzable: %s", exc)
            return False
        ok = 200 <= resp.status_code < 300
        if not ok:
            logger.warning("HEC respondió status %d", resp.status_code)
        return ok

    def _load_spool(self) -> None:
        spool = self._config.spool_path
        if spool is None:
            return
        path = Path(spool)
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    self._queue.appendleft(json.loads(line))
