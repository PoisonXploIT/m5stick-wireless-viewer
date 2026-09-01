"""Contrato de los parsers por firmware."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..models import ObservationEvent, SourceType


class AbstractParser(ABC):
    """Un parser convierte lineas raw del log de un firmware en eventos.

    Contrato:
    - `can_parse` debe ser barato (se llama en modo auto/composite).
    - `parse` devuelve None si la linea no produce evento, aunque `can_parse`
      haya devuelto True (linea aceptable pero incompleta).
    - Los parsers NO deciden el `source`: lo aporta quien invoca (collector),
      porque un mismo parser se usa con fuente serial o file.
    """

    @property
    @abstractmethod
    def firmware_id(self) -> str: ...

    @abstractmethod
    def can_parse(self, line: str) -> bool: ...

    @abstractmethod
    def parse(
        self, line: str, *, received_at: datetime, source: SourceType
    ) -> ObservationEvent | None: ...


class CompositeParser(AbstractParser):
    """Prueba cada parser registrado hasta que uno acepte la linea.

    Permite mezclar lineas de distintos firmwares en el mismo log sin
    configuracion manual (modo `--firmware auto`).
    """

    def __init__(self, parsers: list[AbstractParser]) -> None:
        self._parsers = list(parsers)

    @property
    def firmware_id(self) -> str:
        return "composite"

    def can_parse(self, line: str) -> bool:
        return any(p.can_parse(line) for p in self._parsers)

    def parse(
        self, line: str, *, received_at: datetime, source: SourceType
    ) -> ObservationEvent | None:
        for parser in self._parsers:
            if not parser.can_parse(line):
                continue
            event = parser.parse(line, received_at=received_at, source=source)
            if event is not None:
                return event
        return None
