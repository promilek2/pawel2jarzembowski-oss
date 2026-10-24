from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from termin.config import Settings
from termin.runtime import Transports
from termin.web.app import create_app
from tests.conftest import TODAY
from tests.fake_nfz import FakeNfz

GOOD = "Kolejka-do-okulisty-7"


class Outbox:
    def __init__(self) -> None:
        self.mails: list[tuple[str, str, str]] = []

    async def __call__(self, to: str, subject: str, body: str) -> None:
        self.mails.append((to, subject, body))

    def link_token(self) -> str:
        body = self.mails[-1][2]
        return body.split("token=")[1].split()[0]


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


def make_client(tmp_path: Path, outbox: Outbox | None, **settings_kw: object) -> TestClient:
    options: dict[str, object] = {
        "data_dir": tmp_path,
        "geocoder": "offline",
        "rate_limit": "1000/minute",
        "login_rate_limit": "1000/minute",
        "smtp_host": "smtp.example" if outbox else None,
        "smtp_from": "termin@example.org" if outbox else None,
    }
    options.update(settings_kw)
    settings = Settings(**options)  # type: ignore[arg-type]
    app = create_app(
        settings,
        transports=Transports(nfz=FakeNfz.with_defaults().transport()),
        today=lambda: TODAY,
        mailer=outbox,
    )
    return TestClient(app)


@pytest.fixture
def client(tmp_path: Path, outbox: Outbox) -> Iterator[TestClient]:
    with make_client(tmp_path, outbox) as c:
        yield c


def register(c: TestClient, email: str = "jan@example.com", password: str = GOOD) -> str:
    r = c.post(
        "/api/auth/register",
        json={"email": email, "password": password, "accept_terms": True},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["csrf"])


def test_register_login_logout(client: TestClient, outbox: Outbox) -> None:
    assert client.get("/api/auth/me").json()["user"] is None
    csrf = register(client)
    cookie = client.cookies.get("termin_session")
    assert cookie
    me = client.get("/api/auth/me").json()
    assert me["user"]["email"] == "jan@example.com"
    assert me["user"]["verified"] is False
    assert me["csrf"] == csrf
    assert outbox.mails[0][0] == "jan@example.com"
    assert "weryfikacja" in outbox.mails[0][2]

    r = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert client.get("/api/auth/me").json()["user"] is None

    bad = client.post(
        "/api/auth/login", json={"email": "jan@example.com", "password": "zle-haslo-123"}
    )
    assert bad.status_code == 401
    ok = client.post("/api/auth/login", json={"email": "JAN@example.com", "password": GOOD})
    assert ok.status_code == 200
    assert ok.json()["user"]["email"] == "jan@example.com"


def test_session_cookie_flags(tmp_path: Path) -> None:
    with make_client(tmp_path, None, public_url="https://termin.example") as c:
        r = c.post(
            "/api/auth/register",
            json={"email": "jan@example.com", "password": GOOD, "accept_terms": True},
            headers={"Origin": "https://termin.example"},
        )
        cookie = r.headers["set-cookie"]
        assert cookie.startswith("__Host-termin_session=")
        for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
            assert flag.lower() in cookie.lower(), flag
        assert "Strict-Transport-Security" in r.headers


def test_register_validation(client: TestClient) -> None:
    payload = {"email": "jan@example.com", "password": GOOD, "accept_terms": False}
    assert client.post("/api/auth/register", json=payload).status_code == 400
    payload["accept_terms"] = True
    assert client.post("/api/auth/register", json={**payload, "email": "zly"}).status_code == 400
    weak = client.post("/api/auth/register", json={**payload, "password": "1234567890"})
    assert weak.status_code == 400
    assert "łatwe" in weak.json()["detail"]
    register(client)
    client.cookies.clear()
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_csrf_protection(client: TestClient) -> None:
    csrf = register(client)
    payload = {"q": "okulista", "variant": "dorosli", "where": "Gdynia", "channel": "ntfy",
               "target": "t-abc", "consent": True}  # fmt: skip
    # zalogowany bez nagłówka CSRF → 403
    r = client.post("/api/subscriptions", json=payload)
    assert r.status_code == 403
    assert r.json()["code"] == "csrf"
    # obcy Origin → 403 nawet z tokenem
    evil = client.post(
        "/api/subscriptions",
        json=payload,
        headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
    )
    assert evil.status_code == 403
    ok = client.post("/api/subscriptions", json=payload, headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 201, ok.text
    # niezalogowany (bez ciasteczka) nie potrzebuje tokenu, ale Origin wciąż sprawdzamy
    client.cookies.clear()
    assert client.post("/api/subscriptions", json=payload).status_code == 201
    assert client.post("/stop/xyz", headers={"Origin": "https://evil.example"}).status_code == 403


def test_account_subscriptions_export_delete(client: TestClient) -> None:
    csrf = register(client)
    h = {"X-CSRF-Token": csrf}
    payload = {"q": "okulista", "variant": "dorosli", "where": "Gdynia", "channel": "ntfy",
               "target": "t-abc", "consent": True}  # fmt: skip
    sub = client.post("/api/subscriptions", json=payload, headers=h).json()
    mine = client.get("/api/account/subscriptions").json()
    assert [s["token"] for s in mine] == [sub["token"]]
    assert mine[0]["consent_at"]

    export = client.get("/api/account/export")
    assert export.headers["content-disposition"].startswith("attachment")
    data = json.loads(export.text)
    assert data["account"]["email"] == "jan@example.com"
    assert data["subscriptions"][0]["target"] == "t-abc"

    assert client.delete(f"/api/account/subscriptions/{sub['token']}", headers=h).status_code == 204
    assert client.get("/api/account/subscriptions").json() == []

    sub2 = client.post("/api/subscriptions", json=payload, headers=h).json()
    bad = client.post("/api/account/delete", json={"password": "zle-haslo-123"}, headers=h)
    assert bad.status_code == 400
    gone = client.post("/api/account/delete", json={"password": GOOD}, headers=h)
    assert gone.status_code == 200
    assert client.get("/api/auth/me").json()["user"] is None
    assert client.get(f"/api/subscriptions/{sub2['token']}").status_code == 404  # kaskada
    assert client.get("/api/account/export").status_code == 401


def test_subscription_requires_consent(client: TestClient) -> None:
    payload = {"q": "okulista", "variant": "dorosli", "where": "Gdynia", "channel": "ntfy",
               "target": "t-abc"}  # fmt: skip
    r = client.post("/api/subscriptions", json=payload)
    assert r.status_code == 400
    assert "zgody" in r.json()["detail"]


def test_verify_and_reset_flow(client: TestClient, outbox: Outbox) -> None:
    csrf = register(client)
    token = outbox.link_token()
    assert client.post(
        "/api/auth/verify", json={"token": token}, headers={"X-CSRF-Token": csrf}
    ).json() == {"verified": True}
    assert client.get("/api/auth/me").json()["user"]["verified"] is True

    client.cookies.clear()
    r = client.post("/api/auth/request-reset", json={"email": "jan@example.com"})
    assert r.status_code == 202
    reset_token = outbox.link_token()
    unknown = client.post("/api/auth/request-reset", json={"email": "nikt@example.com"})
    assert unknown.status_code == 202  # ta sama odpowiedź
    assert unknown.json() == r.json()
    weak = client.post("/api/auth/reset", json={"token": reset_token, "password": "1234567890"})
    assert weak.status_code == 400
    done = client.post(
        "/api/auth/reset", json={"token": reset_token, "password": "Calkiem-nowe-haslo-5"}
    )
    assert done.status_code == 200, done.text
    client.cookies.clear()
    again = client.post(
        "/api/auth/reset", json={"token": reset_token, "password": "Calkiem-nowe-haslo-6"}
    )
    assert again.status_code == 400  # link jednorazowy (nie przyjmujemy go drugi raz)
    login = client.post(
        "/api/auth/login", json={"email": "jan@example.com", "password": "Calkiem-nowe-haslo-5"}
    )
    assert login.status_code == 200


def test_change_password_and_logout_all(client: TestClient) -> None:
    csrf = register(client)
    r = client.post(
        "/api/account/password",
        json={"current": "zle-haslo-123", "new": "Inne-dobre-haslo-1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400
    r = client.post(
        "/api/account/password",
        json={"current": GOOD, "new": "Inne-dobre-haslo-1"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    csrf = r.json()["csrf"]
    out = client.post("/api/account/logout-all", headers={"X-CSRF-Token": csrf})
    assert out.status_code == 200
    assert client.get("/api/auth/me").json()["user"] is None


def test_lockout_and_rate_limit(tmp_path: Path) -> None:
    with make_client(tmp_path, None, max_failed_logins=3, login_rate_limit="5/minute") as c:
        register(c)
        c.cookies.clear()
        codes = [
            c.post(
                "/api/auth/login", json={"email": "jan@example.com", "password": "zle-haslo-123"}
            ).status_code
            for _ in range(4)
        ]
        assert codes[:3] == [401, 401, 401]
        assert codes[3] == 429  # konto zablokowane
        # limit na IP (5/min na endpoint logowania): kolejne próby odrzucone niezależnie od konta
        more = [
            c.post("/api/auth/login", json={"email": "x@example.com", "password": GOOD}).status_code
            for _ in range(2)
        ]
        assert more == [401, 429]


def test_without_mail(tmp_path: Path) -> None:
    with make_client(tmp_path, None) as c:
        register(c)
        me = c.get("/api/auth/me").json()
        assert me["mail_enabled"] is False
        c.cookies.clear()
        r = c.post("/api/auth/request-reset", json={"email": "jan@example.com"})
        assert r.status_code == 501


def test_accounts_disabled(tmp_path: Path) -> None:
    with make_client(tmp_path, None, accounts_enabled=False) as c:
        r = c.post(
            "/api/auth/register",
            json={"email": "jan@example.com", "password": GOOD, "accept_terms": True},
        )
        assert r.status_code == 404


def test_body_size_limit(client: TestClient) -> None:
    r = client.post(
        "/api/auth/login", content=b"x" * 70_000, headers={"Content-Type": "application/json"}
    )
    assert r.status_code == 413


def test_security_headers_and_txt(client: TestClient) -> None:
    r = client.get("/")
    for header in ("X-Frame-Options", "Cross-Origin-Opener-Policy", "Content-Security-Policy",
                   "Permissions-Policy", "Referrer-Policy"):  # fmt: skip
        assert header in r.headers, header
    assert "object-src 'none'" in r.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in r.headers  # tylko przy HTTPS
    assert client.get("/api/config").headers["Cache-Control"] == "no-store"
    txt = client.get("/.well-known/security.txt").text
    assert "Expires:" in txt
    assert "Policy:" in txt


def test_legal_pages(tmp_path: Path) -> None:
    with make_client(tmp_path, None) as c:
        for path in ("/regulamin", "/prywatnosc", "/cookies", "/dostepnosc", "/kontakt"):
            r = c.get(path)
            assert r.status_code == 200, path
            assert "Uwaga dla administratora" in r.text  # brak danych operatora jest widoczny
        assert "UZUPEŁNIJ" in c.get("/prywatnosc").text
    with make_client(
        tmp_path / "b",
        None,
        operator_name="Jan <Kowalski>",
        operator_address="ul. Przykładowa 1, 00-001 Warszawa",
        operator_email="kontakt@example.org",
        hosting_provider="Dostawca Serwerów",
    ) as c:
        r = c.get("/prywatnosc")
        assert "UZUPEŁNIJ" not in r.text
        assert "Jan &lt;Kowalski&gt;" in r.text  # escapowanie
        assert "art. 9 ust. 2 lit. a" in r.text
        assert "Prezesa Urzędu Ochrony Danych" in r.text
        assert "termin_session" in c.get("/cookies").text
        assert "art. 8 ustawy" in c.get("/regulamin").text
        assert "mailto:kontakt@example.org" in c.get("/.well-known/security.txt").text
