from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

import pytest

from termin.config import Settings
from termin.core.benefits import Variant
from termin.core.models import GeoPoint
from termin.notify.base import Notification, NotifyError
from termin.notify.channels import (
    DesktopNotifier,
    EmailNotifier,
    NtfyNotifier,
    TelegramNotifier,
    build_notifiers,
)
from termin.notify.subscribe import (
    SubscribeError,
    SubscribeRequest,
    create_subscription,
    parse_date,
)
from termin.notify.watcher import Watcher
from termin.runtime import Runtime, Transports
from termin.store.db import connect
from termin.store.subscriptions import Subscription, SubscriptionStore, new_token, wipe_user_data
from tests.conftest import TODAY
from tests.fake_nfz import FakeNfz
from tests.fake_services import FakeNotifyServices

GDYNIA = GeoPoint(lat=54.5189, lon=18.5305, label="Gdynia", province="11")
NOW = dt.datetime(2026, 10, 24, 9, 0, tzinfo=dt.UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> dt.datetime:
        return self.now


@pytest.fixture
def services() -> FakeNotifyServices:
    return FakeNotifyServices()


@pytest.fixture
async def rt(
    tmp_path: Any, fake_nfz: FakeNfz, services: FakeNotifyServices
) -> AsyncIterator[Runtime]:
    settings = Settings(
        data_dir=tmp_path,
        geocoder="offline",
        telegram_token="123:ABC",
        public_url="https://termin.example",
        notify_cooldown_h=12,
    )
    runtime = Runtime(
        settings,
        transports=Transports(nfz=fake_nfz.transport(), notify=services.transport()),
        conn=connect(":memory:"),
        today=lambda: TODAY,
    )
    yield runtime
    await runtime.aclose()
    runtime.conn.close()


async def subscribe(rt: Runtime, **kw: Any) -> Subscription:
    params: dict[str, Any] = {
        "text": "okulista",
        "channel": "ntfy",
        "target": "termin-test-123",
        "point": GDYNIA,
        "radius_km": 30,
        "consent": True,
    }
    params.update(kw)
    return await create_subscription(rt, SubscribeRequest(**params))


def watcher(rt: Runtime, services: FakeNotifyServices, clock: Clock) -> Watcher:
    return Watcher(rt, build_notifiers(rt.settings, services.transport()), now=clock)


async def test_end_to_end_ntfy_and_telegram(
    rt: Runtime, services: FakeNotifyServices, fake_nfz: FakeNfz
) -> None:
    ntfy_sub = await subscribe(rt)
    tg_sub = await subscribe(rt, channel="telegram", target="4242")
    clock = Clock()
    report = await watcher(rt, services, clock).run_once()

    assert report.notified == 2
    # dwie subskrypcje, to samo zapytanie: jedna grupa na województwo (11 + sąsiednie 14)
    assert report.queries == 2
    pages = [
        (dict(r.url.params)["province"], dict(r.url.params)["page"])
        for r in fake_nfz.requests_to("/queues")
    ]
    assert len(pages) == len(set(pages))

    (msg,) = services.ntfy
    assert msg["topic"] == "termin-test-123"
    # od API 1.4 najkrótsza jest prognoza NFZ (PCUŚ „5 dni”) w Sopocie
    assert "prognoza NFZ 5 dni" in msg["title"]
    assert "Okulista" in msg["title"]
    assert "UL. TESTOWA 33, SOPOT" in msg["message"]
    assert "Tel.: 58 000 00 33" in msg["message"]
    assert "To prognoza NFZ, a nie wolny termin" in msg["message"]
    assert f"https://termin.example/stop/{ntfy_sub.token}" in msg["message"]
    assert "openstreetmap.org" in msg["message"]
    assert [a["label"] for a in msg["actions"]] == ["Mapa", "Przestań obserwować"]

    (tg,) = services.telegram
    assert tg["chat_id"] == "4242"
    assert tg["parse_mode"] == "HTML"
    assert "prognoza NFZ 5 dni" in tg["text"]
    assert f"/stop/{tg_sub.token}" in tg["text"]

    stored = SubscriptionStore(rt.conn).get(ntfy_sub.token)
    assert stored is not None
    assert stored.last_notified_date == dt.date(2026, 10, 29)  # dziś + PCUŚ
    assert stored.last_checked_at == NOW


async def test_groups_identical_queries(
    rt: Runtime, services: FakeNotifyServices, fake_nfz: FakeNfz
) -> None:
    for i in range(5):
        await subscribe(rt, target=f"topic-{i}")
    await watcher(rt, services, Clock()).run_once()
    calls = fake_nfz.requests_to("/queues")
    keys = {(dict(c.url.params)["province"], dict(c.url.params)["page"]) for c in calls}
    assert len(calls) == len(keys)  # żadnego zdublowanego żądania
    assert len(services.ntfy) == 5


async def test_dedup_and_cooldown(
    rt: Runtime, services: FakeNotifyServices, fake_nfz: FakeNfz
) -> None:
    await subscribe(rt)
    clock = Clock()
    w = watcher(rt, services, clock)
    assert (await w.run_once()).notified == 1

    # ten sam termin: nie powiadamiamy ponownie
    clock.now += dt.timedelta(hours=24)
    report = await w.run_once()
    assert report.notified == 0
    assert report.not_better == 1

    # pojawia się wcześniejszy termin, ale jesteśmy w cooldownie
    item = fake_nfz.items["11"][0]
    item["attributes"]["dates"]["date"] = "2026-10-28"
    rt.cache._conn.execute("DELETE FROM http_cache")  # świeże dane z NFZ
    clock.now = NOW + dt.timedelta(hours=1)
    SubscriptionStore(rt.conn).mark_notified(
        SubscriptionStore(rt.conn).active()[0].token, dt.date(2026, 11, 3), "x", clock.now
    )
    report = await w.run_once()
    assert report.cooldown == 1
    assert len(services.ntfy) == 1

    # po cooldownie: powiadamiamy o lepszym terminie
    clock.now += dt.timedelta(hours=13)
    report = await w.run_once()
    assert report.notified == 1
    assert "28.10.2026" in services.ntfy[-1]["title"]


async def test_before_date_and_province_area(rt: Runtime, services: FakeNotifyServices) -> None:
    await subscribe(rt, before_date=dt.date(2026, 10, 27))  # nic tak wcześnie
    await subscribe(
        rt, point=None, radius_km=None, province="11", target="woj", text="okulista dla dzieci"
    )
    report = await watcher(rt, services, Clock()).run_once()
    assert report.no_match == 1
    assert report.notified == 1
    assert services.ntfy[0]["topic"] == "woj"
    assert "dla dzieci" in services.ntfy[0]["title"]


async def test_expired_baseline_resets(rt: Runtime, services: FakeNotifyServices) -> None:
    sub = await subscribe(rt)
    store = SubscriptionStore(rt.conn)
    store.mark_notified(sub.token, dt.date(2026, 10, 1), "old", NOW - dt.timedelta(days=30))
    report = await watcher(rt, services, Clock()).run_once()
    assert report.notified == 1


async def test_channel_errors_are_reported(rt: Runtime, services: FakeNotifyServices) -> None:
    await subscribe(rt)
    await subscribe(rt, channel="email", target="a@example.com")
    services.fail = True
    report = await watcher(rt, services, Clock()).run_once()
    assert report.notified == 0
    assert any("ntfy" in e for e in report.errors)
    assert any("email" in e for e in report.errors)  # e-mail nieskonfigurowany
    sub = SubscriptionStore(rt.conn).active()[0]
    assert sub.last_notified_date is None  # spróbujemy ponownie


async def test_nfz_errors_do_not_crash(
    rt: Runtime, services: FakeNotifyServices, fake_nfz: FakeNfz
) -> None:
    await subscribe(rt)
    rt.client._settings = rt.settings.model_copy(update={"nfz_max_retries": 1})
    fake_nfz.fail_with = [503] * 50
    report = await watcher(rt, services, Clock()).run_once()
    assert report.errors
    assert report.notified == 0


async def test_run_forever_stops(rt: Runtime, services: FakeNotifyServices) -> None:
    import asyncio

    stop = asyncio.Event()
    w = watcher(rt, services, Clock())
    calls = 0
    original = w.run_once

    async def counting() -> Any:
        nonlocal calls
        calls += 1
        stop.set()
        return await original()

    w.run_once = counting  # type: ignore[method-assign]
    await asyncio.wait_for(w.run_forever(stop), timeout=5)
    assert calls == 1


async def test_consent_is_required(rt: Runtime) -> None:
    with pytest.raises(SubscribeError, match="zgody"):
        await subscribe(rt, consent=False)
    sub = await subscribe(rt)
    assert sub.consent_at is not None


async def test_subscribe_validation(rt: Runtime) -> None:
    with pytest.raises(SubscribeError, match="kanał"):
        await subscribe(rt, channel="sms")
    with pytest.raises(SubscribeError, match="dokąd"):
        await subscribe(rt, target="  ")
    with pytest.raises(SubscribeError, match="przyszłości"):
        await subscribe(rt, before_date=TODAY)
    with pytest.raises(SubscribeError, match="Doprecyzuj"):
        await subscribe(rt, text="kolano")
    with pytest.raises(SubscribeError, match="miejsce"):
        await subscribe(rt, point=None)
    with pytest.raises(SubscribeError, match="Nie udało się"):
        await subscribe(rt, point=None, where="Pcim Dolny")
    with pytest.raises(SubscribeError, match="województwa"):
        await subscribe(rt, point=None, province="99")
    sub = await subscribe(rt, channel="desktop", target="")
    assert sub.variant is Variant.ADULT  # dwuznaczność rozstrzygnięta domyślnie


def test_parse_date() -> None:
    assert parse_date("31.12.2026") == dt.date(2026, 12, 31)
    assert parse_date("2026-12-31") == dt.date(2026, 12, 31)
    with pytest.raises(SubscribeError):
        parse_date("kiedyś")


async def test_store_privacy_and_wipe(rt: Runtime) -> None:
    sub = await subscribe(rt, point=GeoPoint(lat=54.518934, lon=18.530512))
    assert sub.lat == 54.52 and sub.lon == 18.53  # zaokrąglone do ~1 km
    public = sub.public_dict()
    assert "target" not in public
    assert public["token"] == sub.token
    store = SubscriptionStore(rt.conn)
    assert len(store.for_target("ntfy", "termin-test-123")) == 1
    with pytest.raises(ValueError, match="kanał"):
        store.add(
            Subscription(
                token=new_token(),
                label="x",
                benefit_query="X",
                channel="sms",
                target="t",
                province="11",
            )
        )
    with pytest.raises(ValueError, match="województwa"):
        store.add(
            Subscription(
                token=new_token(), label="x", benefit_query="X", channel="ntfy", target="t"
            )
        )
    rt.conn.execute("INSERT INTO geocode_cache VALUES ('h', 1, 2, 'Gdynia', '11', '2026-10-24')")
    counts = wipe_user_data(rt.conn)
    assert counts["subscriptions"] == 1
    assert counts["geocode_cache"] == 1
    assert {"users", "sessions", "email_tokens"} <= counts.keys()
    assert store.active() == []
    assert store.delete("nope") is False


async def test_ntfy_payload_and_errors() -> None:
    sent: list[Any] = []

    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    n = NtfyNotifier("https://ntfy.example/", httpx.MockTransport(handler))
    await n.send("t", Notification(title="Ż", body="b", click_url="https://x"))
    assert str(sent[0].url) == "https://ntfy.example"
    bad = NtfyNotifier("https://ntfy.example", httpx.MockTransport(lambda r: httpx.Response(429)))
    with pytest.raises(NotifyError):
        await bad.send("t", Notification(title="a", body="b"))

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x", request=request)

    with pytest.raises(NotifyError):
        await NtfyNotifier("https://x", httpx.MockTransport(boom)).send("t", Notification("a", "b"))
    tg = TelegramNotifier("1:A", httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(NotifyError):
        await tg.send("1", Notification(title="<b>", body="&"))


async def test_email_notifier(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            self.host = host

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def starttls(self) -> None:
            sent.append("tls")

        def login(self, user: str, password: str) -> None:
            sent.append("login")

        def send_message(self, msg: Any) -> None:
            sent.append(msg)

    monkeypatch.setattr("termin.notify.channels.smtplib.SMTP", FakeSMTP)
    settings = Settings(
        smtp_host="smtp.example", smtp_from="termin@example.org", smtp_user="u", smtp_password="p"
    )
    await EmailNotifier(settings).send("a@b.pl", Notification(title="Termin", body="Treść"))
    assert sent[:2] == ["tls", "login"]
    assert sent[2]["To"] == "a@b.pl"
    assert "email" in build_notifiers(settings)
    with pytest.raises(NotifyError):
        EmailNotifier(Settings())

    class Broken(FakeSMTP):
        def send_message(self, msg: Any) -> None:
            raise OSError("down")

    monkeypatch.setattr("termin.notify.channels.smtplib.SMTP", Broken)
    with pytest.raises(NotifyError):
        await EmailNotifier(settings).send("a@b.pl", Notification(title="T", body="B"))


async def test_desktop_notifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("termin.notify.channels.shutil.which", lambda _: None)
    with pytest.raises(NotifyError, match="notify-send"):
        await DesktopNotifier().send("", Notification(title="a", body="b"))
    monkeypatch.setattr("termin.notify.channels.shutil.which", lambda _: "/bin/true")
    await DesktopNotifier().send("", Notification(title="a", body="b"))
    monkeypatch.setattr("termin.notify.channels.shutil.which", lambda _: "/bin/false")
    with pytest.raises(NotifyError):
        await DesktopNotifier().send("", Notification(title="a", body="b"))
