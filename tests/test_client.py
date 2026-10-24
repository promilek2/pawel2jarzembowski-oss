from __future__ import annotations

import httpx
import pytest

from termin import USER_AGENT
from termin.config import Settings
from termin.core.cache import MemoryCache
from termin.core.client import (
    NfzBadRequestError,
    NfzClient,
    NfzError,
    NfzUnavailableError,
    QueueQuery,
)
from tests.fake_nfz import MANY_PLACES_ID, FakeNfz

Q = QueueQuery(benefit="PORADNIA OKULISTYCZNA", province="11")


async def test_fetches_all_pages_and_sends_required_params(
    client: NfzClient, fake_nfz: FakeNfz
) -> None:
    entries = await client.queues(Q)
    assert len(entries) == 30
    queue_calls = fake_nfz.requests_to("/queues")
    assert len(queue_calls) == 2
    params = dict(queue_calls[0].url.params)
    assert params["format"] == "json"
    assert params["api-version"] == "1.3"
    assert params["limit"] == "25"
    assert params["province"] == "11"
    assert params["case"] == "1"
    assert queue_calls[0].headers["User-Agent"] == USER_AGENT
    assert "github.com" in USER_AGENT


async def test_cache_prevents_second_request(client: NfzClient, fake_nfz: FakeNfz) -> None:
    await client.queues(Q)
    await client.queues(Q)
    assert len(fake_nfz.requests_to("/queues")) == 2  # tylko pierwsze wywołanie


async def test_retries_on_429_and_5xx(client: NfzClient, fake_nfz: FakeNfz) -> None:
    fake_nfz.fail_with = [503, 429, 502]
    page = await client.queues_page(Q)
    assert len(page.entries) == 25
    assert len(fake_nfz.calls) == 4


async def test_gives_up_after_max_retries(settings: Settings, fake_nfz: FakeNfz) -> None:
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    fake_nfz.fail_with = [503] * 10
    async with NfzClient(settings, transport=fake_nfz.transport(), sleep=fake_sleep) as c:
        with pytest.raises(NfzUnavailableError):
            await c.queues_page(Q)
    assert len(fake_nfz.calls) == settings.nfz_max_retries
    assert len(sleeps) == settings.nfz_max_retries - 1
    assert sleeps == sorted(sleeps)  # backoff rośnie


async def test_network_errors_are_retried(settings: Settings) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json={"data": [], "meta": {"count": 0}})

    async def no_sleep(_: float) -> None:
        return None

    async with NfzClient(settings, transport=httpx.MockTransport(handler), sleep=no_sleep) as c:
        page = await c.queues_page(Q)
    assert page.entries == ()
    assert calls == 3


async def test_bad_request_is_not_retried(client: NfzClient, fake_nfz: FakeNfz) -> None:
    with pytest.raises(NfzBadRequestError, match="province"):
        await client.queues_page(QueueQuery(benefit="X", province="99"))
    assert len(fake_nfz.calls) == 1


async def test_invalid_json(settings: Settings) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))
    async with NfzClient(settings, transport=transport) as c:
        with pytest.raises(NfzError, match="JSON"):
            await c.queues_page(Q)


async def test_concurrency_never_exceeds_four(settings: Settings, fake_nfz: FakeNfz) -> None:
    import asyncio

    big = [{**item, "id": f"{item['id']}-{n}"} for n in range(8) for item in fake_nfz.items["11"]]
    fake_nfz.items["11"] = big  # 240 pozycji = 10 stron
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return fake_nfz.handle(request)

    async with NfzClient(settings, transport=httpx.MockTransport(handler)) as c:
        entries = await c.queues(Q)
    assert len(entries) == 240
    assert peak <= 4


async def test_page_cap(settings: Settings, fake_nfz: FakeNfz) -> None:
    async with NfzClient(settings, transport=fake_nfz.transport()) as c:
        entries = await c.queues(Q, max_pages=1)
    assert len(entries) == 25


async def test_sequential_pagination_without_count(settings: Settings) -> None:
    pages = {
        "1": {"data": [], "links": {"next": "x"}},
        "2": {"data": [], "links": {"next": "y"}},
        "3": {"data": [], "links": {"next": None}},
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        seen.append(page)
        return httpx.Response(200, json=pages[page])

    async with NfzClient(settings, transport=httpx.MockTransport(handler)) as c:
        await c.queues(Q)
    assert seen == ["1", "2", "3"]


async def test_single_queue_many_places_and_dictionaries(client: NfzClient) -> None:
    entry = await client.queue("00000001-0000-4000-8000-000000000001")
    assert entry is not None and entry.locality == "GDYNIA"
    places = await client.many_places(MANY_PLACES_ID)
    assert len(places) == 2
    benefits = await client.benefits("okul")
    assert "PORADNIA OKULISTYCZNA DLA DZIECI" in benefits
    assert await client.localities("gdy", province="11") == ["GDYNIA", "GDYNIA (MIASTO)"]


async def test_dictionary_pagination(settings: Settings, fake_nfz: FakeNfz) -> None:
    fake_nfz.benefit_names = [f"PORADNIA {i}" for i in range(60)]
    async with NfzClient(settings, cache=MemoryCache(), transport=fake_nfz.transport()) as c:
        names = await c.benefits("PORADNIA")
    assert len(names) == 60
