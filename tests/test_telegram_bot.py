from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from termin.config import Settings
from termin.notify.telegram_bot import TelegramBot, parse_query, updates_payload
from termin.runtime import Runtime, Transports
from termin.store.db import connect
from termin.store.subscriptions import SubscriptionStore
from tests.conftest import TODAY
from tests.fake_nfz import FakeNfz
from tests.fake_services import FakeNotifyServices


@pytest.fixture
async def bot(
    tmp_path: Any, fake_nfz: FakeNfz
) -> AsyncIterator[tuple[TelegramBot, FakeNotifyServices]]:
    services = FakeNotifyServices()
    rt = Runtime(
        Settings(data_dir=tmp_path, geocoder="offline", telegram_token="1:A"),
        transports=Transports(nfz=fake_nfz.transport()),
        conn=connect(":memory:"),
        today=lambda: TODAY,
    )
    b = TelegramBot(rt, "1:A", transport=services.transport())
    yield b, services
    await b.aclose()
    await rt.aclose()
    rt.conn.close()


def last(services: FakeNotifyServices) -> str:
    return str(services.telegram[-1]["text"])


@pytest.mark.parametrize(
    ("text", "what", "where", "before", "radius"),
    [
        ("okulista; Gdynia", "okulista", "Gdynia", None, None),
        ("okulista w Gdyni", "okulista", "Gdyni", None, None),
        ("rezonans; Gdańsk; przed 31.12.2026; 40 km", "rezonans", "Gdańsk", "31.12.2026", 40.0),
        ("", "", None, None, None),
    ],
)
def test_parse_query(
    text: str, what: str, where: str | None, before: str | None, radius: float | None
) -> None:
    q = parse_query(text)
    assert (q.what, q.where, q.before, q.radius_km) == (what, where, before, radius)


async def test_help_and_unknown(bot: tuple[TelegramBot, FakeNotifyServices]) -> None:
    b, services = bot
    await b.handle("1", "/start")
    assert "/szukaj" in last(services)
    await b.handle("1", "/cokolwiek")
    assert "Nie znam" in last(services)


async def test_search(bot: tuple[TelegramBot, FakeNotifyServices]) -> None:
    b, services = bot
    await b.handle("7", "/szukaj okulsta w Gdyni")
    text = last(services)
    assert "Okulista" in text
    assert "03.11.2026" in text
    assert "Tel.:" in text
    assert "stan na" in text
    await b.handle("7", "/szukaj okulista")
    assert "Napisz np." in last(services)
    await b.handle("7", "/szukaj kolano; Gdynia")
    assert "Doprecyzuj" in last(services)
    await b.handle("7", "/szukaj okulista; Pcim Dolny")
    assert "Nie udało się znaleźć" in last(services)
    await b.handle("7", "/szukaj rezonans; Gdynia")
    assert "Nie znalazłem" in last(services)


async def test_watch_list_stop(bot: tuple[TelegramBot, FakeNotifyServices]) -> None:
    b, services = bot
    await b.handle("9", "/obserwuj okulista; Gdynia; przed 31.12.2026; 30 km")
    assert "Obserwuję" in last(services)
    store = SubscriptionStore(b.runtime.conn)
    (sub,) = store.for_target("telegram", "9")
    assert sub.radius_km == 30
    assert sub.before_date is not None

    await b.handle("9", "/lista")
    assert sub.token in last(services)
    await b.handle("10", "/stop " + sub.token)  # cudzy token
    assert "Nie znalazłem" in last(services)
    await b.handle("9", "/stop " + sub.token)
    assert "Przestałem" in last(services)
    await b.handle("9", "/lista")
    assert "Nic nie obserwujesz" in last(services)

    await b.handle("9", "/obserwuj okulista; Gdynia")
    await b.handle("9", "/obserwuj rezonans; Sopot")
    await b.handle("9", "/stop wszystko")
    assert "Usunięto obserwacje: 2" in last(services)
    await b.handle("9", "/obserwuj okulista")
    assert "Napisz np." in last(services)
    await b.handle("9", "/obserwuj okulista; Gdynia; przed wczoraj")
    assert "Nie rozumiem daty" in last(services)


async def test_poll_once(bot: tuple[TelegramBot, FakeNotifyServices]) -> None:
    b, services = bot
    services.telegram_updates = updates_payload((5, "1", "/pomoc"), (6, "2", "/lista"))["result"]
    assert await b.poll_once(long_poll_s=0) == 2
    assert b.offset == 7
    assert len(services.telegram) == 2
    services.fail = True
    assert await b.poll_once(long_poll_s=0) == 0
