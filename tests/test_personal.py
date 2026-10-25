"""Funkcje dla zalogowanych: „Sprawdziłem telefonicznie”, sprawy, notatki, Web Push."""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi.testclient import TestClient

from termin.config import Settings
from termin.notify.base import Notification
from termin.notify.watcher import Watcher, polish_hour
from termin.notify.webpush import (
    PushNotifier,
    allowed_endpoint,
    b64url,
    b64url_decode,
    encrypt,
    load_vapid,
    vapid_authorization,
)
from termin.runtime import Runtime, Transports
from termin.store.db import connect
from termin.store.personal import CaseStore
from termin.store.push import PushStore
from termin.web.app import create_app
from tests.conftest import TODAY
from tests.fake_nfz import FakeNfz
from tests.test_web_auth import GOOD

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


# ---------------------------------------------------------------------- atrapa przeglądarki


class Browser:
    """Klucze urządzenia (jak w `pushManager.subscribe`) i odszyfrowanie (RFC 8291)."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = os.urandom(16)
        self.public = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )

    def subscription(self, endpoint: str = ENDPOINT) -> dict[str, Any]:
        return {
            "endpoint": endpoint,
            "keys": {"p256dh": b64url(self.public), "auth": b64url(self.auth)},
        }

    def decrypt(self, body: bytes) -> dict[str, Any]:
        salt, rs, idlen = body[:16], int.from_bytes(body[16:20], "big"), body[20]
        as_public = body[21 : 21 + idlen]
        assert rs == 4096
        shared = self.key.exchange(
            ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
        )

        def hkdf(salt_: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
            return HKDF(algorithm=hashes.SHA256(), length=n, salt=salt_, info=info).derive(ikm)

        ikm = hkdf(self.auth, shared, b"WebPush: info\x00" + self.public + as_public, 32)
        cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(cek).decrypt(nonce, body[21 + idlen :], None)
        assert plain.endswith(b"\x02")
        data: dict[str, Any] = json.loads(plain[:-1])
        return data


class FakePushService:
    def __init__(self, browser: Browser) -> None:
        self.browser = browser
        self.received: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.status = 201

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["content-encoding"] == "aes128gcm"
        self.headers.append(request.headers)
        if self.status < 300:
            self.received.append(self.browser.decrypt(request.content))
        return httpx.Response(self.status)


@pytest.fixture
def browser() -> Browser:
    return Browser()


@pytest.fixture
def push_service(browser: Browser) -> FakePushService:
    return FakePushService(browser)


@pytest.fixture
def web(tmp_path: Path, push_service: FakePushService) -> Iterator[TestClient]:
    settings = Settings(
        data_dir=tmp_path,
        geocoder="offline",
        rate_limit="1000/minute",
        login_rate_limit="1000/minute",
        public_url="https://termin.example.org",
    )
    app = create_app(
        settings,
        transports=Transports(
            nfz=FakeNfz.with_defaults().transport(), notify=push_service.transport()
        ),
        today=lambda: TODAY,
    )
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def login(c: TestClient, email: str = "jan@example.com") -> dict[str, str]:
    r = c.post("/api/auth/register", json={"email": email, "password": GOOD, "accept_terms": True})
    assert r.status_code == 201, r.text
    return {"X-CSRF-Token": r.json()["csrf"]}


def consent(c: TestClient, h: dict[str, str]) -> None:
    assert c.post("/api/me/health-consent", json={"consent": True}, headers=h).status_code == 200


# ---------------------------------------------------------------------- kryptografia


def test_encrypt_roundtrip(browser: Browser) -> None:
    sub = browser.subscription()
    body = encrypt(b'{"title":"x"}', sub["keys"]["p256dh"], sub["keys"]["auth"])
    assert browser.decrypt(body) == {"title": "x"}
    with pytest.raises(ValueError, match="klucze"):
        encrypt(b"x", "zly-klucz", sub["keys"]["auth"])


def test_vapid_jwt_is_valid_es256(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    settings = Settings(data_dir=tmp_path, security_contact="bezpieczenstwo@example.org")
    keys = load_vapid(conn, settings)
    assert load_vapid(conn, settings).public_key == keys.public_key  # klucz trwały w bazie
    header = vapid_authorization(ENDPOINT, keys, now=1_000_000)
    token, k = header.removeprefix("vapid t=").split(", k=")
    assert k == keys.public_key
    h, p, s = token.split(".")
    claims = json.loads(b64url_decode(p))
    assert claims == {"aud": "https://fcm.googleapis.com", "exp": 1_000_000 + 12 * 3600,
                      "sub": "mailto:bezpieczenstwo@example.org"}  # fmt: skip
    raw = b64url_decode(s)
    signature = encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64url_decode(k))
    public.verify(signature, f"{h}.{p}".encode(), ec.ECDSA(hashes.SHA256()))
    conn.close()


def test_configured_vapid_key(tmp_path: Path) -> None:
    raw = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    conn = connect(tmp_path / "t.db")
    keys = load_vapid(conn, Settings(data_dir=tmp_path, vapid_private_key=raw))
    assert keys.private_raw() == raw
    assert conn.execute("SELECT count(*) FROM app_secrets").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        (ENDPOINT, True),
        ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
        ("https://web.push.apple.com/QGx", True),
        ("https://db5p.notify.windows.com/w/?token=x", True),
        ("http://fcm.googleapis.com/fcm/send/x", False),
        ("https://fcm.googleapis.com.evil.example/x", False),
        ("https://evil.example/fcm.googleapis.com", False),
        ("https://user@fcm.googleapis.com/x", False),
        ("https://fcm.googleapis.com:8443/x", False),
        ("https://169.254.169.254/latest/meta-data", False),
    ],
)
def test_allowed_endpoint(url: str, ok: bool) -> None:
    assert allowed_endpoint(url) is ok


# ---------------------------------------------------------------------- API: dostęp i zgoda


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/me/features", None),
        ("get", "/api/cases", None),
        ("post", "/api/cases", {"label": "okulista"}),
        ("post", "/api/reports", {"queue_id": "q1", "reached": False}),
        ("get", "/api/reports/summary?ids=q1", None),
        ("get", "/api/notes?ids=q1", None),
        ("put", "/api/notes/q1", {"note": "x"}),
        ("post", "/api/push/test", None),
    ],
)
def test_requires_login(web: TestClient, method: str, path: str, body: Any) -> None:
    r = web.request(method.upper(), path, json=body)
    assert r.status_code == 401


def test_writes_require_health_consent(web: TestClient) -> None:
    h = login(web)
    r = web.post("/api/cases", json={"label": "okulista"}, headers=h)
    assert r.status_code == 428
    assert web.get("/api/me/features").json()["health_consent"] is False
    consent(web, h)
    assert web.post("/api/cases", json={"label": "okulista"}, headers=h).status_code == 201
    assert web.get("/api/me/features").json()["health_consent"] is True


def test_withdraw_consent_deletes_personal_data(web: TestClient) -> None:
    h = login(web)
    consent(web, h)
    web.post("/api/cases", json={"label": "okulista"}, headers=h)
    web.put("/api/notes/q1", json={"note": "dzwonić rano"}, headers=h)
    web.post("/api/reports", json={"queue_id": "q1", "reached": False}, headers=h)
    r = web.post("/api/me/health-consent", json={"consent": False}, headers=h)
    assert r.json()["removed"] == {"cases": 1, "facility_notes": 1, "phone_reports": 1}
    assert web.get("/api/cases").json() == []


# ---------------------------------------------------------------------- „Sprawdziłem telefonicznie”


def test_phone_reports_summary(tmp_path: Path, web: TestClient) -> None:
    h1 = login(web, "a@example.com")
    consent(web, h1)
    base = {"queue_id": "q-1", "nfz_date": "2026-11-03"}
    r = web.post("/api/reports", json={**base, "reached": True, "outcome": "same"}, headers=h1)
    assert r.status_code == 201, r.text
    # poprawka tego samego dnia nadpisuje, nie dubluje
    r = web.post(
        "/api/reports",
        json={**base, "reached": True, "outcome": "other", "offered_date": "2026-11-23"},
        headers=h1,
    )
    assert r.json()["summary"]["reports"] == 1
    web.post("/api/auth/logout", headers=h1)
    h2 = login(web, "b@example.com")
    consent(web, h2)
    web.post("/api/reports", json={**base, "reached": False}, headers=h2)
    web.post(
        "/api/reports", json={"queue_id": "q-2", "reached": True, "outcome": "none"}, headers=h2
    )
    summary = web.get("/api/reports/summary?ids=q-1,q-2,zla!id").json()
    assert summary["q-1"] == {
        "reports": 2, "not_reached": 1, "same": 0, "other": 1, "none": 0,
        "median_delta_days": 20, "last_day": "2026-10-24", "days": 14,
    }  # fmt: skip
    assert summary["q-2"]["none"] == 1
    assert "zla!id" not in summary


@pytest.mark.parametrize(
    "body",
    [
        {"queue_id": "q1", "reached": True},  # brak wyniku
        {"queue_id": "q1", "reached": True, "outcome": "other"},  # brak daty
        {"queue_id": "q1", "reached": True, "outcome": "other", "offered_date": "2020-01-01"},
        {"queue_id": "q 1;drop", "reached": False},
    ],
)
def test_phone_report_validation(web: TestClient, body: dict[str, Any]) -> None:
    h = login(web)
    consent(web, h)
    assert web.post("/api/reports", json=body, headers=h).status_code == 400


# ---------------------------------------------------------------------- sprawy i notatki


def test_cases_crud_and_ics(web: TestClient) -> None:
    h = login(web)
    consent(web, h)
    r = web.post(
        "/api/cases",
        json={"label": "Okulista", "queue_id": "q-1", "provider": "Przychodnia Oko",
              "address": "ul. Morska 1, Gdynia", "phone": "58 000 00 00"},
        headers=h,
    )  # fmt: skip
    case = r.json()
    assert case["status"] == "to_call" and case["visit_date"] is None
    assert web.get(f"/api/cases/{case['id']}/ics").status_code == 404
    r = web.patch(
        f"/api/cases/{case['id']}",
        json={"status": "booked", "visit_date": "2026-11-20", "note": "pokój 12"},
        headers=h,
    )
    assert r.json()["status"] == "booked" and r.json()["visit_date"] == "2026-11-20"
    ics = web.get(f"/api/cases/{case['id']}/ics")
    assert "DTSTART;VALUE=DATE:20261120" in ics.text and "Wizyta: Okulista" in ics.text
    r = web.patch(f"/api/cases/{case['id']}", json={"clear_visit_date": True}, headers=h)
    assert r.json()["visit_date"] is None
    assert (
        web.patch(f"/api/cases/{case['id']}", json={"status": "zly"}, headers=h).status_code == 422
    )
    assert web.patch("/api/cases/999", json={"status": "done"}, headers=h).status_code == 404
    assert web.delete(f"/api/cases/{case['id']}", headers=h).status_code == 204
    assert web.get("/api/cases").json() == []


def test_cases_are_private(web: TestClient) -> None:
    h1 = login(web, "a@example.com")
    consent(web, h1)
    case_id = web.post("/api/cases", json={"label": "Kardiolog"}, headers=h1).json()["id"]
    web.put("/api/notes/q-1", json={"note": "tajne"}, headers=h1)
    web.post("/api/auth/logout", headers=h1)
    h2 = login(web, "b@example.com")
    consent(web, h2)
    assert web.get("/api/cases").json() == []
    assert (
        web.patch(f"/api/cases/{case_id}", json={"status": "done"}, headers=h2).status_code == 404
    )
    assert web.delete(f"/api/cases/{case_id}", headers=h2).status_code == 404
    assert web.get("/api/notes?ids=q-1").json() == {}


def test_notes(web: TestClient) -> None:
    h = login(web)
    consent(web, h)
    r = web.put("/api/notes/q-1", json={"note": "  dzwonić po 7:30  "}, headers=h)
    assert r.json()["note"] == "dzwonić po 7:30"
    assert web.get("/api/notes?ids=q-1,q-2").json() == {"q-1": "dzwonić po 7:30"}
    web.put("/api/notes/q-1", json={"note": ""}, headers=h)
    assert web.get("/api/notes?ids=q-1").json() == {}
    export = web.get("/api/account/export").json()
    assert export["personal"]["facility_notes"] == []


# ---------------------------------------------------------------------- Web Push


def test_push_subscribe_and_test(
    web: TestClient, browser: Browser, push_service: FakePushService
) -> None:
    h = login(web)
    features = web.get("/api/me/features").json()["push"]
    assert features["enabled"] and len(b64url_decode(features["public_key"])) == 65
    bad = {
        **browser.subscription("https://evil.example/push"),
    }
    assert web.post("/api/push/subscribe", json=bad, headers=h).status_code == 400
    r = web.post("/api/push/subscribe", json=browser.subscription(), headers=h)
    assert r.status_code == 201 and r.json()["devices"] == 1
    assert web.post("/api/push/test", headers=h).status_code == 200
    assert push_service.received[0]["title"] == "Termin: powiadomienia działają"
    assert push_service.headers[0]["authorization"].startswith("vapid t=")
    # urządzenie wypisane po stronie usługi push (410) znika z bazy
    push_service.status = 410
    assert web.post("/api/push/test", headers=h).status_code == 400
    assert web.get("/api/me/features").json()["push"]["devices"] == 0


def test_push_unsubscribe(web: TestClient, browser: Browser) -> None:
    h = login(web)
    web.post("/api/push/subscribe", json=browser.subscription(), headers=h)
    r = web.post("/api/push/unsubscribe", json={"endpoint": ENDPOINT}, headers=h)
    assert r.json()["devices"] == 0


def test_watch_with_push_channel(web: TestClient, browser: Browser) -> None:
    body = {"q": "okulista", "variant": "dorosli", "where": "Gdynia", "radius": 30,
            "channel": "push", "target": "push", "consent": True}  # fmt: skip
    assert web.post("/api/subscriptions", json=body).status_code == 401
    h = login(web)
    r = web.post("/api/subscriptions", json=body, headers=h)
    assert r.status_code == 400  # brak urządzenia z powiadomieniami
    web.post("/api/push/subscribe", json=browser.subscription(), headers=h)
    r = web.post("/api/subscriptions", json=body, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["channel"] == "push"


# ---------------------------------------------------------------------- przypomnienia o wizytach


async def test_visit_reminder_once_and_only_daytime(
    runtime: Runtime, browser: Browser, push_service: FakePushService
) -> None:
    conn = runtime.conn
    from termin.store.accounts import AccountStore

    user = AccountStore(conn).create("jan@example.com", GOOD)
    PushStore(conn).add(
        user.id, ENDPOINT, *browser.subscription()["keys"].values(), "Chrome · Android"
    )
    cases = CaseStore(conn)
    tomorrow = TODAY + dt.timedelta(days=1)
    cases.create(
        user.id,
        {"label": "Okulista", "provider": "Oko", "visit_date": tomorrow, "status": "booked"},
    )
    cases.create(user.id, {"label": "Stara", "visit_date": tomorrow, "status": "done"})
    notifier = PushNotifier(conn, runtime.settings, push_service.transport())

    night = dt.datetime(2026, 10, 24, 22, 30, tzinfo=dt.UTC)  # 00:30 w Polsce
    assert polish_hour(night) not in range(8, 21)
    report = await Watcher(runtime, {"push": notifier}, now=lambda: night).run_once()
    assert report.reminders == 0 and push_service.received == []

    day = dt.datetime(2026, 10, 24, 8, 0, tzinfo=dt.UTC)  # 10:00 w Polsce
    report = await Watcher(runtime, {"push": notifier}, now=lambda: day).run_once()
    assert report.reminders == 1
    assert push_service.received[0]["title"] == "Jutro wizyta: Okulista"
    report = await Watcher(runtime, {"push": notifier}, now=lambda: day).run_once()
    assert report.reminders == 0  # bez powtórek


async def test_push_notifier_without_devices(runtime: Runtime) -> None:
    from termin.notify.base import NotifyError

    with pytest.raises(NotifyError):
        await PushNotifier(runtime.conn, runtime.settings).send("1", Notification("t", "b"))


async def test_oversized_payload_keeps_device(runtime: Runtime, browser: Browser) -> None:
    from termin.store.accounts import AccountStore

    user = AccountStore(runtime.conn).create("jan@example.com", GOOD)
    store = PushStore(runtime.conn)
    store.add(user.id, ENDPOINT, *browser.subscription()["keys"].values(), None)
    notifier = PushNotifier(runtime.conn, runtime.settings)
    async with httpx.AsyncClient() as client:
        assert not await notifier.send_to(store.for_user(user.id)[0], b"x" * 5000, client)
    assert store.count(user.id) == 1
