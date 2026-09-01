"""Modelos de datos normalizados (Fase 1).

Notas de diseño:
- `Network` es mutable y no frozen: el store lo actualiza con cada observacion.
- `Observation` es la base frozen de los eventos; NO lleva campos por defecto
  porque las subclases (`NetworkSeen`, `ClientAssociated`) anaden campos sin
  default, y dataclass no permite un campo sin default despues de uno con
  default en la jerarquia de herencia.
- Todo datetime es aware (UTC). Prohibido `datetime.utcnow` (deprecated).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

SourceType = Literal["serial", "file"]


def utc_now() -> datetime:
    """Instante actual en UTC, aware."""
    return datetime.now(timezone.utc)


_MAC_RE = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$")


def normalize_mac(mac: str) -> str:
    """Normaliza una MAC a minúsculas y con separador ':'.

    Acepta 'AA-BB-CC-DD-EE-FF', 'AABB.CC.DD.EE.FF' o 'AABBCCDDEEFF'.
    Lanza ValueError si no es una MAC de 6 octetos hexadecimales.
    """
    cleaned = mac.strip().lower()
    if not _MAC_RE.match(cleaned):
        compact = re.sub(r"[-:. ]", "", cleaned)
        if len(compact) == 12 and all(c in "0123456789abcdef" for c in compact):
            cleaned = ":".join(compact[i : i + 2] for i in range(0, 12, 2))
        else:
            raise ValueError(f"MAC invalida: {mac!r}")
    return cleaned


@dataclass(slots=True, frozen=True)
class Client:
    """Un cliente (dispositivo) observado asociado a una red."""

    mac: str
    bssid: str | None = None  # red a la que esta asociado
    first_seen: datetime = field(default_factory=utc_now)
    last_seen: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class Network:
    """Estado actual de una red, deduplicada por BSSID."""

    bssid: str
    ssid: str | None
    channel: int | None
    rssi: int | None
    n_clients: int = 0
    clients: set[str] = field(default_factory=set)
    first_seen: datetime = field(default_factory=utc_now)
    last_seen: datetime = field(default_factory=utc_now)


@dataclass(slots=True, frozen=True)
class Observation:
    """Evento base emitido por un parser tras leer una linea.

    Los eventos concretos son `NetworkSeen` y `ClientAssociated`.
    """

    timestamp: datetime
    firmware: str
    source: SourceType
    raw_line: str


@dataclass(slots=True, frozen=True)
class NetworkSeen(Observation):
    """Una red fue detectada en esta linea."""

    network: Network


@dataclass(slots=True, frozen=True)
class ClientAssociated(Observation):
    """Un cliente fue observado asociado a una red en esta linea."""

    client: Client


# Union discriminada: cada evento sabe que tipo de dato transporta.
ObservationEvent = NetworkSeen | ClientAssociated
