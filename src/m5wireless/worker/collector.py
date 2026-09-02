"""Colector: orquesta la pipeline source -> parser -> store."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from ..models import ObservationEvent, SourceType, utc_now
from ..parser.base import AbstractParser
from ..source.base import AbstractSource
from ..store.base import AbstractStore

logger = logging.getLogger(__name__)


class Collector:
    """Une una fuente, un parser y un store.

    El callback de linea es sincrono (por contrato de :class:`AbstractSource`),
    asi que el colector procesa cada linea en bloque: parsear -> aplicar al
    store. Los errores de una linea no derriban la pipeline: se cuentan y se
    registran, y se sigue con la siguiente.

    - ``source_type``: etiqueta ('serial' | 'file') que el parser mete en los
      eventos; lo aporta quien orquesta porque el mismo flujo puede venir de
      serial o de archivo.
    - ``clock``: reloj inyectable (default :func:`utc_now`) para que los tests
      sean deterministas; se usa como ``received_at`` del parser.
    """

    def __init__(
        self,
        source: AbstractSource,
        parser: AbstractParser,
        store: AbstractStore,
        *,
        source_type: SourceType = "file",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._source = source
        self._parser = parser
        self._store = store
        self._source_type = source_type
        self._clock = clock if clock is not None else utc_now
        self._stats = {"lines": 0, "events": 0, "errors": 0}
        self._observers: list[Callable[[ObservationEvent], None]] = []

    @property
    def source_type(self) -> SourceType:
        return self._source_type

    def observe(self, callback: Callable[[ObservationEvent], None]) -> None:
        """Registra un observador de eventos en vivo (p. ej. el hub SSE o el
        exporter Splunk HEC).

        Se invoca tras cada `store.apply` exitoso, desde el hilo que procesa
        la linea (puede ser el hilo lector serial): el callback debe ser
        thread-safe. Admite varios observadores; se invocan en orden.
        """
        self._observers.append(callback)

    async def run(self) -> None:
        """Arranca la fuente y procesa lineas hasta que termine o se pare."""
        await self._source.start(self._on_line)

    async def stop(self) -> None:
        await self._source.stop()

    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def status(self) -> dict[str, object]:
        """Estado de conexion para el dashboard: estado de la fuente + parser."""
        info = dict(self._source.status())
        info["firmware"] = self._parser.firmware_id
        return info

    # ---- interno ----
    def _on_line(self, line: str) -> None:
        self._stats["lines"] += 1
        try:
            event = self._parser.parse(line, received_at=self._clock(), source=self._source_type)
        except Exception:  # una linea mala no para la pipeline.
            self._stats["errors"] += 1
            logger.exception("error parseando linea: %r", line)
            return
        if event is None:
            return
        try:
            self._store.apply(event)
        except Exception:  # un fallo de store no para la pipeline.
            self._stats["errors"] += 1
            logger.exception("error aplicando evento al store")
            return
        self._stats["events"] += 1
        for observer in self._observers:
            try:
                observer(event)
            except Exception:  # un fallo del observador no para la pipeline.
                self._stats["errors"] += 1
                logger.exception("error en el observador de eventos")
