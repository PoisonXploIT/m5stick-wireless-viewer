"""Contrato de los stores (memoria y SQLite).

El store mantiene dos vistas:

- **Estado actual**: `networks` (PK por bssid) y `clients` (PK por mac). Un
  cliente solo aparece una vez, asociado a la ultima red vista. Es una foto
  del "ahora".
- **Historico completo**: `observations`, append-only, una fila por evento.
  Aqui vive la traza completa de asociaciones/desasociaciones y de las lecturas
  de senal/canal en el tiempo.

Los metodos de escritura son **sincronos** (los stores son locales y rapidos);
el colector los invoca desde su callback de linea, que tambien es sincrono.
`apply(event)` es la entrada unica: a partir de un `ObservationEvent` actualiza
estado e historico. Los metodos de upsert reciben `at` (el timestamp del
evento) para que el estado refleje el tiempo de los datos y no el reloj del
sistema, lo que mantiene los tests deterministas.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from ..models import (
    Client,
    ClientAssociated,
    Network,
    NetworkSeen,
    ObservationEvent,
    SourceType,
    StatusEvent,
)


def event_to_observation_row(event: ObservationEvent) -> ObservationRow:
    """Convierte un `ObservationEvent` en una fila plana del historico."""
    if isinstance(event, NetworkSeen):
        return ObservationRow(
            timestamp=event.timestamp,
            firmware=event.firmware,
            source=event.source,
            event_type="network_seen",
            bssid=event.network.bssid,
            rssi=event.network.rssi,
            client_mac=None,
            raw_line=event.raw_line,
        )
    if isinstance(event, ClientAssociated):
        return ObservationRow(
            timestamp=event.timestamp,
            firmware=event.firmware,
            source=event.source,
            event_type="client_associated",
            bssid=event.client.bssid,
            rssi=None,
            client_mac=event.client.mac,
            raw_line=event.raw_line,
        )
    raise TypeError(f"evento desconocido: {event!r}")


@dataclass(frozen=True)
class ObservationRow:
    """Fila del historico (tabla ``observations``), como se devuelve en consultas."""

    timestamp: datetime
    firmware: str
    source: SourceType
    event_type: str  # 'network_seen' | 'client_associated'
    bssid: str | None
    rssi: int | None
    client_mac: str | None
    raw_line: str


class AbstractStore(ABC):
    """Almacenamiento de estado actual + historico de observaciones."""

    # ---- escritura (implementada por cada store) ----
    @abstractmethod
    def upsert_network(self, network: Network, *, at: datetime) -> None: ...

    @abstractmethod
    def upsert_client(self, client: Client, *, at: datetime) -> None: ...

    @abstractmethod
    def record_observation(self, event: ObservationEvent) -> None: ...

    # ---- consulta ----
    @abstractmethod
    def get_network(self, bssid: str) -> Network | None: ...

    @abstractmethod
    def get_client(self, mac: str) -> Client | None: ...

    @abstractmethod
    def iter_observations(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Iterator[ObservationRow]: ...

    @abstractmethod
    def get_recent_observations(self, limit: int) -> list[ObservationRow]: ...

    @abstractmethod
    def get_networks(
        self, *, since: datetime | None = None, until: datetime | None = None
    ) -> list[Network]: ...

    @abstractmethod
    def get_clients(self, *, associated_to: str | None = None) -> list[Client]: ...

    @abstractmethod
    def get_network_history(
        self,
        bssid: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[ObservationRow]: ...

    @abstractmethod
    def get_channel_distribution(self) -> dict[int, int]: ...

    # ---- mantenimiento ----
    @abstractmethod
    def prune_older_than(self, days: int, *, reference: datetime | None = None) -> int: ...

    # ---- orquestacion (concreta): punto unico de entrada ----
    def apply(self, event: ObservationEvent) -> None:
        """Aplica un evento a estado + historico.

        - `NetworkSeen`       -> upsert_network + record_observation
        - `ClientAssociated`  -> upsert_client   + record_observation
        - `StatusEvent`       -> no-op (el estado del firmware no se persiste;
          fluye solo a los observadores en vivo)
        """
        if isinstance(event, StatusEvent):
            return
        if isinstance(event, NetworkSeen):
            self.upsert_network(event.network, at=event.timestamp)
        elif isinstance(event, ClientAssociated):
            self.upsert_client(event.client, at=event.timestamp)
        else:  # pragma: no cover - la union es exhaustiva
            raise TypeError(f"evento desconocido para el store: {event!r}")
        self.record_observation(event)

    def close(self) -> None:
        """Liberar recursos. Sin efecto en MemoryStore; SQLite cierra la conexion."""
