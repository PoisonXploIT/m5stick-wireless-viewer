r"""Parser de la consola de Bruce (M5Stick + firmware Bruce, CLI tipo Flipper).

El serial de Bruce NO es un stream continuo: escribe eventos puntuales de
ciclo de vida y espera a comandos. Formato real observado (fixture local
``data/bruce_capture.log``, no commiteado por contener datos reales; copia
sin datos sensibles en ``tests/fixtures/bruce_console.log``):

    [   7.8s] Selected: Sniffer
    [   8.4s] [1845679][E][sd_diskio.cpp:761] sdcard_mount(): f_mount failed: (3) The physical drive cannot work
    [   8.9s] SDCard in a different Bus, using sdcardSPI instance
    [   8.9s] SDCARD NOT mounted, check wiring and format
    [   9.1s] Sniffer started!

Lineas de ciclo de vida -> `StatusEvent` (no son observaciones de red: no se
persisten en el historico ni en Splunk; fluyen a los observadores/SSE).

Solo se implementan los patrones vistos en el fixture real. Pendientes con
muestras reales (ver SEGUIMIENTO.md): lineas de resultados de scan a pantalla
(`NetworkSeen`) y rutas de fichero escritas por Bruce.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..models import ObservationEvent, SourceType, StatusEvent
from .base import AbstractParser
from .registry import register_parser

# Prefijo de timestamp de la consola Bruce: `[   7.8s] `.
_PREFIX_RE = re.compile(r"^\[\s*[\d.]+s\]\s*")

# Patrones validados contra el fixture real (data/bruce_capture.log).
_SELECTED_RE = re.compile(r"^Selected:\s*(.+)$")
_SNIFFER_STARTED_RE = re.compile(r"^Sniffer started!$")
_SDCARD_NOT_MOUNTED_RE = re.compile(r"^SDCARD NOT mounted.*$", re.IGNORECASE)
# Log de error ESP-IDF: `[1845679][E][sd_diskio.cpp:761] mensaje...`
_ESPIDF_ERROR_RE = re.compile(r"^\[\d+\]\[E\]\[[^\]]+\]:?\s*(.*)$")


@register_parser
class BruceConsoleParser(AbstractParser):
    """Eventos de ciclo de vida de la consola Bruce -> `StatusEvent`.

    Stateless: una linea, a lo sume un evento. Las lineas que no reconocen
    (p. ej. el prompt o ruido) devuelven ``None``.
    """

    @property
    def firmware_id(self) -> str:
        return "bruce"

    def can_parse(self, line: str) -> bool:
        return self._message(line.strip()) is not None

    def parse(
        self, line: str, *, received_at: datetime, source: SourceType
    ) -> ObservationEvent | None:
        message = self._message(line.strip())
        if message is None:
            return None
        return StatusEvent(
            timestamp=received_at,
            firmware=self.firmware_id,
            source=source,
            raw_line=line.strip(),
            message=message,
        )

    # ---- interno ----
    @staticmethod
    def _message(line: str) -> str | None:
        """Mensaje de estado en espanol, o ``None`` si la linea no es evento."""
        body = _PREFIX_RE.sub("", line).strip()
        if not body:
            return None
        match = _SELECTED_RE.match(body)
        if match is not None:
            return f"menu: seleccionado {match.group(1).strip()}"
        if _SNIFFER_STARTED_RE.match(body):
            return "sniffer iniciado"
        if _SDCARD_NOT_MOUNTED_RE.match(body):
            return "SD no montada: revisar cableado y formato (o seguir con LittleFS)"
        match = _ESPIDF_ERROR_RE.match(body)
        if match is not None:
            return f"error ESP-IDF: {match.group(1).strip()}"
        return None
