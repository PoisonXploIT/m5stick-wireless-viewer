"""Sistema de parsers por firmware.

Importar este paquete registra los parsers disponibles en el registro
global (`registry`). Los stubs (wifi_duck, hash_monster, packet_monitor)
NO se registran: solo tienen `can_parse -> False` y servirian como plantilla
cuando haya fixtures reales.
"""

from __future__ import annotations

# Importar los parsers reales dispara su registro via @register_parser.
from . import (
    evil_m5project,  # noqa: F401
    marauder,  # noqa: F401
)
from .base import AbstractParser, CompositeParser
from .registry import ParserRegistry, get_parser, registry

__all__ = [
    "AbstractParser",
    "CompositeParser",
    "ParserRegistry",
    "get_parser",
    "registry",
]
