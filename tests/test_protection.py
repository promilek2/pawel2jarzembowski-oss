"""Ochrona przed przeciążeniem i nadużyciami (docs/SECURITY_REVIEW.md)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from termin import security
from termin.config import Settings
from termin.core.geo import Geocoder
from termin.notify.telegram_bot import TelegramBot
from termin.runtime import Runtime, Transports
from termin.store.db import connect
from termin.web.app import create_app
from termin.web.protection import IpRateLimiter, MailThrottle
from tests.conftest import TODAY
from tests.fake_nfz import FakeNfz
from tests.fake_services import FakeNotifyServices

SUB = {"q": "okulista", "variant": "dorosli", "where": "Gdynia", "channel": "ntfy",
       "target": "t-abc", "consent": True}  # fmt: skip


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make(
    tmp_path: Path, transport: httpx.AsyncBaseTransport | None = None, **kw: Any
) -> TestClient:
    options: dict[str, Any] = {
        "data_dir": tmp_path,
        "geocoder": "offline",
        "rate_limit": "1000/minute",
    }
    options.update(kw)
    app = create_app(
        Settings(**options),
        transports=Transports(nfz=transport or FakeNfz.with_defaults().transport()),
        today=lambda: TODAY,
    )
    return TestClient(app)


def test_ip_rate_limiter_window_and_memory_cap() -> None:
    clock = Clock()
    limiter = IpRateLimiter(per_minute=3, max_ips=2, clock=clock)
    assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]
    clock.t += 61
    assert limiter.allow("a")
    limiter.allow("b")
    limiter.allow("c")  # przekroczony limit adresów: najstarszy („a”) wypada z pamięci
    assert len(limiter._windows) == 2
    assert "a" not in limiter._windows


def test_mail_throttle() -> None:
    clock = Clock()
    throttle = MailThrottle(per_hour=2, clock=clock)
    assert [throttle.allow("Jan@x.pl"), throttle.allow("jan@x.pl"), throttle.allow("jan@x.pl")] == [
        True, True, False,
    ]  # fmt: skip
    assert throttle.allow("ktos@x.pl")
    clock.t += 3601
    assert throttle.allow("jan@x.pl")


def test_global_rate_limit(tmp_path: Path) -> None:
    with make(tmp_path, global_requests_per_minute=10) as c:
        codes = [c.get("/api/config").status_code for _ in range(12)]
        assert codes[:10] == [200] * 10
        assert codes[10:] == [429, 429]
        assert c.get("/healthz").status_code == 200  # monitoring nie jest limitowany
        assert c.get("/static/app.js").status_code == 429  # dotyczy wszystkich ścieżek


def test_body_limit_including_chunked(tmp_path: Path) -> None:
    with make(tmp_path, max_body_kb=4) as c:
        big = b'{"email": "' + b"x" * 8000 + b'"}'
        assert (
            c.post(
                "/api/auth/login", content=big, headers={"Content-Type": "application/json"}
            ).status_code
            == 413
        )

        def chunks() -> Iterator[bytes]:  # bez Content-Length (Transfer-Encoding: chunked)
            for _ in range(10):
                yield b"x" * 1024

        r = c.post(
            "/api/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
        )
        assert r.status_code == 413


def test_search_concurrency_limit(tmp_path: Path) -> None:
    with make(tmp_path, max_concurrent_searches=1, search_queue_timeout_s=0.05) as c:
        slots: asyncio.Semaphore = c.app.state.search_slots  # type: ignore[attr-defined]
        slots._value = 0  # wszystkie sloty zajęte (symulacja trwającego zalewu)
        r = c.get("/api/search", params={"q": "okulista dorośli", "where": "Gdynia"})
        assert r.status_code == 503
        assert r.headers["Retry-After"] == "10"
        slots._value = 1
        assert (
            c.get("/api/search", params={"q": "okulista dorośli", "where": "Gdynia"}).status_code
            == 200
        )


def test_search_timeout(tmp_path: Path) -> None:
    fake = FakeNfz.with_defaults()

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.5)
        return fake.handle(request)

    with make(tmp_path, httpx.MockTransport(slow), search_timeout_s=0.1) as c:
        r = c.get("/api/search", params={"q": "okulista dorośli", "where": "Gdynia"})
        assert r.status_code == 504


def test_fallback_query_page_cap(tmp_path: Path) -> None:
    fake = FakeNfz.with_defaults()
    item = fake.items["11"][0]
    fake.add_items(
        "11",
        [{**item, "id": f"spam-{i}", "attributes": {**item["attributes"], "benefit": "QWXZ"}}
         for i in range(400)],
    )  # fmt: skip
    with make(tmp_path, fake.transport()) as c:
        c.get("/api/search", params={"q": "qwxz", "province": "11"})
    pages = [r for r in fake.requests_to("/queues") if "QWXZ" in str(r.url)]
    assert len(pages) <= 4  # 16 stron dostępnych, pobieramy maks. 4


def test_subscription_limits(tmp_path: Path) -> None:
    with make(tmp_path, max_subscriptions_per_target=2, subscribe_rate_limit="100/hour") as c:
        assert [c.post("/api/subscriptions", json=SUB).status_code for _ in range(3)] == [
            201,
            201,
            400,
        ]
        assert c.post("/api/subscriptions", json={**SUB, "target": "inny"}).status_code == 201
    with make(tmp_path / "b", max_subscriptions_total=1, subscribe_rate_limit="100/hour") as c:
        assert c.post("/api/subscriptions", json=SUB).status_code == 201
        r = c.post("/api/subscriptions", json={**SUB, "target": "x"})
        assert r.status_code == 400
        assert "limit" in r.json()["detail"]


def test_subscribe_rate_limit(tmp_path: Path) -> None:
    with make(tmp_path, subscribe_rate_limit="2/hour") as c:
        codes = [
            c.post("/api/subscriptions", json={**SUB, "target": f"t{i}"}).status_code
            for i in range(3)
        ]
        assert codes == [201, 201, 429]


def test_hashing_queue_overflow_returns_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(security, "HASH_QUEUE_MAX", 0)
    with make(tmp_path, login_rate_limit="100/minute") as c:
        r = c.post("/api/auth/login", json={"email": "a@example.com", "password": "Cokolwiek-123"})
        assert r.status_code == 503
        assert r.json()["code"] == "busy"


async def test_hashing_does_not_block_event_loop() -> None:
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        for _ in range(20):
            ticks += 1
            await asyncio.sleep(0.005)

    await asyncio.gather(
        ticker(), *(security.verify_password_async("x", security.DUMMY_HASH) for _ in range(4))
    )
    assert ticks == 20  # pętla działała w trakcie liczenia scrypt


def test_trusted_hosts(tmp_path: Path) -> None:
    with make(tmp_path, allowed_hosts=["termin.example"]) as c:
        assert c.get("/healthz").status_code == 400  # Host: testserver
        assert c.get("/healthz", headers={"Host": "termin.example"}).status_code == 200


async def test_nominatim_queue_overflow_falls_back(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=[])

    clock = Clock()
    geo = Geocoder(
        Settings(data_dir=tmp_path, geocoder="auto", nominatim_max_wait_s=3),
        transport=httpx.MockTransport(handler),
        clock=clock,
    )
    Geocoder._next_slot = clock.t + 100  # kolejka zapchana na 100 s
    point = await geo.geocode("Gdynia")
    await geo.aclose()
    Geocoder._next_slot = 0.0
    assert point is not None
    assert point.source == "offline"
    assert calls == []  # nie ustawiliśmy się w kolejce


async def test_telegram_flood_is_ignored(tmp_path: Path) -> None:
    services = FakeNotifyServices()
    rt = Runtime(
        Settings(data_dir=tmp_path, geocoder="offline", telegram_commands_per_minute=5),
        transports=Transports(nfz=FakeNfz.with_defaults().transport()),
        conn=connect(":memory:"),
        today=lambda: TODAY,
    )
    bot = TelegramBot(rt, "1:A", transport=services.transport())
    for _ in range(12):
        await bot.handle("666", "/pomoc")
    await bot.handle("777", "/pomoc")
    await bot.aclose()
    await rt.aclose()
    rt.conn.close()
    assert len([m for m in services.telegram if m["chat_id"] == "666"]) == 5
    assert len([m for m in services.telegram if m["chat_id"] == "777"]) == 1


def test_injection_inputs_are_harmless(tmp_path: Path) -> None:
    """SQLi/XSS w parametrach: zapytania parametryzowane, a odpowiedzi to JSON."""
    with make(tmp_path) as c:
        evil = "okulista'; DROP TABLE subscriptions; --<script>alert(1)</script>"
        r = c.get("/api/search", params={"q": evil, "province": "11"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        assert c.post("/api/subscriptions", json=SUB).status_code == 201  # tabela istnieje
        page = c.get("/stop/<script>alert(1)</script>")
        assert "<script>alert(1)</script>" not in page.text
        assert c.get("/static/../../pyproject.toml").status_code == 404  # path traversal
