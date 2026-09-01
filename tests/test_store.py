"""Tests del store (memoria y SQLite), parametrizados sobre ambos backends."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from m5wireless.models import Client, ClientAssociated, Network, NetworkSeen
from m5wireless.store import AbstractStore, MemoryStore, SQLiteStore

NOW = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(seconds=30)
OLD = NOW - timedelta(days=40)

BSSID_A = "aa:bb:cc:dd:ee:01"
BSSID_B = "de:ad:be:ef:00:99"


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path) -> AbstractStore:
    if request.param == "memory":
        result: AbstractStore = MemoryStore()
    else:
        result = SQLiteStore(tmp_path / "store.db")
    yield result
    result.close()


def _net(bssid: str, ssid=None, channel=None, rssi=None) -> Network:
    return Network(bssid=bssid, ssid=ssid, channel=channel, rssi=rssi)


def _network_seen(net: Network, at: datetime) -> NetworkSeen:
    return NetworkSeen(
        timestamp=at, firmware="marauder", source="file", raw_line="x", network=net
    )


def _client_associated(mac: str, bssid: str | None, at: datetime) -> ClientAssociated:
    return ClientAssociated(
        timestamp=at,
        firmware="evil_m5project",
        source="file",
        raw_line="x",
        client=Client(mac=mac, bssid=bssid),
    )


def test_upsert_network_new_and_update(store: AbstractStore) -> None:
    store.upsert_network(_net(BSSID_A, "Net", 6, -70), at=NOW)
    (first,) = store.get_networks()
    assert first.ssid == "Net"
    assert first.channel == 6
    assert first.rssi == -70
    assert first.first_seen == NOW
    assert first.last_seen == NOW

    # Una segunda lectura con ssid/channel None NO debe pisar los valores previos;
    # solo rssi y last_seen cambian.
    store.upsert_network(_net(BSSID_A, None, None, -50), at=LATER)
    (updated,) = store.get_networks()
    assert updated.ssid == "Net"
    assert updated.channel == 6
    assert updated.rssi == -50
    assert updated.first_seen == NOW
    assert updated.last_seen == LATER


def test_upsert_client_and_reassociation(store: AbstractStore) -> None:
    store.upsert_client(Client(mac="ff:ee:dd:cc:bb:aa", bssid=BSSID_A), at=NOW)
    store.upsert_client(Client(mac="ff:ee:dd:cc:bb:aa", bssid=BSSID_B), at=LATER)

    (client,) = store.get_clients()
    assert client.mac == "ff:ee:dd:cc:bb:aa"
    assert client.bssid == BSSID_B  # ultima asociacion gana.
    assert client.first_seen == NOW
    assert client.last_seen == LATER


def test_apply_network_seen_updates_state_and_history(store: AbstractStore) -> None:
    store.apply(_network_seen(_net(BSSID_A, "Net", 6, -70), at=NOW))

    (net,) = store.get_networks()
    assert net.bssid == BSSID_A
    history = store.get_network_history(BSSID_A)
    assert len(history) == 1
    assert history[0].event_type == "network_seen"
    assert history[0].rssi == -70
    assert history[0].client_mac is None


def test_apply_client_associated_updates_state_and_history(store: AbstractStore) -> None:
    store.apply(_client_associated("ff:ee:dd:cc:bb:aa", BSSID_A, at=NOW))

    (client,) = store.get_clients()
    assert client.bssid == BSSID_A
    history = store.get_network_history(BSSID_A)
    assert len(history) == 1
    assert history[0].event_type == "client_associated"
    assert history[0].client_mac == "ff:ee:dd:cc:bb:aa"


def test_get_clients_filter_by_bssid(store: AbstractStore) -> None:
    store.upsert_client(Client(mac="aa:aa:aa:aa:aa:aa", bssid=BSSID_A), at=NOW)
    store.upsert_client(Client(mac="bb:bb:bb:bb:bb:bb", bssid=BSSID_B), at=NOW)

    only_b = store.get_clients(associated_to=BSSID_B)
    assert [c.mac for c in only_b] == ["bb:bb:bb:bb:bb:bb"]


def test_get_networks_computes_current_clients(store: AbstractStore) -> None:
    store.upsert_network(_net(BSSID_A, "NetA", 6, -70), at=NOW)
    store.upsert_network(_net(BSSID_B, "NetB", 11, -54), at=NOW)
    store.upsert_client(Client(mac="c1", bssid=BSSID_A), at=NOW)
    store.upsert_client(Client(mac="c2", bssid=BSSID_A), at=NOW)
    store.upsert_client(Client(mac="c3", bssid=BSSID_B), at=NOW)

    by_bssid = {n.bssid: n for n in store.get_networks()}
    assert by_bssid[BSSID_A].n_clients == 2
    assert by_bssid[BSSID_A].clients == {"c1", "c2"}
    assert by_bssid[BSSID_B].n_clients == 1


def test_get_channel_distribution(store: AbstractStore) -> None:
    store.upsert_network(_net("aa:00:00:00:00:01", None, 6, -70), at=NOW)
    store.upsert_network(_net("aa:00:00:00:00:02", None, 6, -71), at=NOW)
    store.upsert_network(_net("aa:00:00:00:00:03", None, 11, -54), at=NOW)
    # una red sin canal no cuenta.
    store.upsert_network(_net("aa:00:00:00:00:04", None, None, -60), at=NOW)

    assert store.get_channel_distribution() == {6: 2, 11: 1}


def test_get_network_history_time_range(store: AbstractStore) -> None:
    store.apply(_network_seen(_net(BSSID_A, "Net", 6, -70), at=NOW))
    store.apply(_network_seen(_net(BSSID_A, "Net", 6, -50), at=LATER))

    assert len(store.get_network_history(BSSID_A)) == 2
    assert len(store.get_network_history(BSSID_A, since=LATER)) == 1
    assert len(store.get_network_history(BSSID_A, until=NOW)) == 1
    # rango que no cubre nada.
    assert store.get_network_history(BSSID_A, since=LATER, until=NOW) == []


def test_prune_older_than_keeps_recent_drops_old(store: AbstractStore) -> None:
    store.apply(_network_seen(_net("aa:00:00:00:00:10", None, 6, -70), at=OLD))
    store.apply(_network_seen(_net("aa:00:00:00:00:11", None, 6, -70), at=OLD))
    store.apply(_network_seen(_net(BSSID_A, "Net", 6, -70), at=NOW))

    removed = store.prune_older_than(30, reference=NOW)
    assert removed == 2
    # el historico total quedo solo con la observacion reciente.
    all_rows: list = []
    for bssid in ("aa:00:00:00:00:10", "aa:00:00:00:00:11", BSSID_A):
        all_rows.extend(store.get_network_history(bssid))
    assert len(all_rows) == 1


def test_get_recent_observations_limit_and_order(store: AbstractStore) -> None:
    for i in range(5):
        store.apply(
            NetworkSeen(
                timestamp=NOW + timedelta(seconds=i),
                firmware="marauder",
                source="file",
                raw_line=f"line {i}",
                network=_net(BSSID_A, "Net", 6, -70),
            )
        )

    recent = store.get_recent_observations(3)
    # las 3 ultimas, de la mas antigua a la mas reciente.
    assert [r.raw_line for r in recent] == ["line 2", "line 3", "line 4"]
    # limit >= total devuelve todo; limit <= 0 no devuelve nada.
    assert len(store.get_recent_observations(10)) == 5
    assert store.get_recent_observations(0) == []
