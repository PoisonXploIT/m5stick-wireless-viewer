"""Contrato comun de las fuentes de datos (serial y archivo)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

# Callback que recibe cada linea raw (sin newline) leida por la fuente.
LineCallback = Callable[[str], None]


class AbstractSource(ABC):
    """Fuente de lineas de log.

    - `start(callback)` abre la fuente y empieza a entregar lineas al callback
      hasta que se pare (o termine, en el caso de una reproduccion unica).
    - `stop()` pide el cese; `start` debe terminar poco despues.

    El parser NO decide el `source`: lo aporta quien orquesta (el colector),
    porque un mismo flujo puede venir de serial o de archivo.
    """

    @abstractmethod
    async def start(self, callback: LineCallback) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    def status(self) -> dict[str, object]:
        """Estado de la fuente para el dashboard (p. ej. puerto, estado).

        Implementacion por defecto minima; las fuentes concretas lo amplian.
        """
        return {"state": "desconocido"}
