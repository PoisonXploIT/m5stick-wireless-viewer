"""Registro de parsers por firmware.

El registro almacena CLASES, no instancias: `get` devuelve una instancia
fresca cada vez. Esto es importante porque los parsers pueden ser stateful
(p. ej. EvilM5ProjectParser recuerda el ultimo BSSID) y no queremos estado
compartido entre ejecuciones o tests.
"""

from __future__ import annotations

from .base import AbstractParser, CompositeParser


class ParserRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, type[AbstractParser]] = {}

    def register(self, cls: type[AbstractParser]) -> None:
        firmware_id = cls().firmware_id
        if firmware_id in self._factories:
            raise ValueError(
                f"parser ya registrado para {firmware_id!r}: "
                f"{self._factories[firmware_id].__name__}"
            )
        self._factories[firmware_id] = cls

    def get(self, firmware_id: str) -> AbstractParser:
        try:
            return self._factories[firmware_id]()
        except KeyError:
            known = ", ".join(sorted(self._factories)) or "(ninguno)"
            raise ValueError(
                f"firmware desconocido: {firmware_id!r}. Disponibles: {known}"
            ) from None

    def all_factories(self) -> list[type[AbstractParser]]:
        return list(self._factories.values())

    def composite(self) -> CompositeParser:
        """Parser que acepta lineas de cualquier firmware registrado."""
        return CompositeParser([cls() for cls in self._factories.values()])


# Registro global del paquete.
registry = ParserRegistry()


def register_parser(cls: type[AbstractParser]) -> type[AbstractParser]:
    """Decorador: registra la clase en el registro global.

    Uso:
        @register_parser
        class MarauderParser(AbstractParser): ...
    """
    registry.register(cls)
    return cls


def get_parser(firmware_id: str) -> AbstractParser:
    if firmware_id == "auto":
        return registry.composite()
    return registry.get(firmware_id)
