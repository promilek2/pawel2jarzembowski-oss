"""Testy na PRAWDZIWYM API NFZ. Domyślnie pomijane: `pytest -m live`."""

from __future__ import annotations

import pytest

from termin.config import Settings
from termin.core.cache import NullCache
from termin.core.client import NfzClient, QueueQuery

pytestmark = pytest.mark.live


@pytest.fixture
async def live_client():  # type: ignore[no-untyped-def]
    async with NfzClient(Settings(), cache=NullCache()) as c:
        yield c


async def test_queues_contract(live_client: NfzClient) -> None:
    page = await live_client.queues_page(
        QueueQuery(benefit="PORADNIA OKULISTYCZNA", province="11", case=1)
    )
    assert page.count is not None and page.count > 0
    assert page.skipped == 0
    entry = page.entries[0]
    assert "OKULISTYCZNA" in entry.benefit
    assert entry.provider
    with_dates = [e for e in page.entries if e.dates is not None]
    assert with_dates, "spodziewamy się, że część placówek ma termin"
    assert any(e.as_at for e in with_dates)


async def test_single_queue_and_many_places(live_client: NfzClient) -> None:
    entries = await live_client.queues(
        QueueQuery(benefit="PORADNIA OKULISTYCZNA", province="07"), max_pages=4
    )
    assert entries
    single = await live_client.queue(entries[0].id)
    assert single is not None and single.id == entries[0].id
    many = [e for e in entries if e.many_places]
    if many:
        places = await live_client.many_places(many[0].id)
        assert places, "many-places=Y powinno zwrócić lokalizacje"


async def test_dictionaries(live_client: NfzClient) -> None:
    benefits = await live_client.benefits("okul")
    assert any("OKULISTYCZNA" in b for b in benefits)
    localities = await live_client.localities("gdy", province="11")
    assert any("GDYNIA" in loc for loc in localities)
