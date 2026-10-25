"""Kody odzyskiwania, weryfikacja dwuetapowa, sesje, „nie wylogowuj mnie”, zmiana e-maila."""

from __future__ import annotations

import datetime as dt
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from termin.security import (
    device_label,
    new_recovery_code,
    normalize_recovery_code,
    totp_match,
    totp_now,
    totp_uri,
)
from termin.store.accounts import AccountError, AccountStore
from termin.store.db import connect
from tests.test_web_auth import GOOD, Outbox, make_client

NEW = "Nowe-haslo-do-kolejki-9"
PHONE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/130.0 Mobile Safari/537.36"
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def nomail(tmp_path: Path) -> Iterator[TestClient]:
    """Instancja bez poczty (jak darmowy Render)."""
    with make_client(tmp_path, None) as c:
        yield c


@pytest.fixture
def mail(tmp_path: Path, outbox: Outbox) -> Iterator[TestClient]:
    with make_client(tmp_path, outbox) as c:
        yield c


@pytest.fixture
def outbox() -> Outbox:
    return Outbox()


def register(c: TestClient, email: str = "jan@example.com") -> dict[str, object]:
    r = c.post(
        "/api/auth/register",
        json={"email": email, "password": GOOD, "accept_terms": True},
        headers={"user-agent": PHONE_UA},
    )
    assert r.status_code == 201, r.text
    body: dict[str, object] = r.json()
    return body


def csrf(c: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": str(c.get("/api/auth/me").json()["csrf"])}


def enable_2fa(c: TestClient) -> str:
    r = c.post("/api/account/2fa/setup", json={"password": GOOD}, headers=csrf(c))
    assert r.status_code == 200, r.text
    data = r.json()
    secret = data["secret"].replace(" ", "")
    assert data["uri"].startswith("otpauth://totp/Termin:jan@example.com?secret=")
    assert data["qr"] and set("".join(data["qr"])) <= {"0", "1"}
    code = totp_now(secret, time.time())
    r = c.post("/api/account/2fa/enable", json={"code": code}, headers=csrf(c))
    assert r.status_code == 200, r.text
    return secret


def logout(c: TestClient) -> None:
    c.post("/api/auth/logout", headers=csrf(c))


# ---------------------------------------------------------------------- czyste funkcje


def test_totp_matches_rfc6238_vector() -> None:
    # RFC 6238, dodatek B: sekret "12345678901234567890", T=59 → 94287082 (8 cyfr) → 287082
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp_now(secret, 59) == "287082"
    assert totp_match(secret, "287 082", 59) == 1
    assert totp_match(secret, "287082", 59 + 30) == 1  # okno ±1 krok
    assert totp_match(secret, "287082", 59 + 90) is None
    assert totp_match(secret, "287082", 59, after_step=1) is None  # powtórka
    assert totp_match(secret, "abc", 59) is None
    assert "issuer=Termin" in totp_uri(secret, "a@b.pl")


def test_recovery_code_format() -> None:
    code = new_recovery_code()
    assert len(code) == 19 and code.count("-") == 3
    assert normalize_recovery_code(code.lower().replace("-", " ")) == code
    assert normalize_recovery_code("0000-0000-0000-0000") is None
    assert normalize_recovery_code("za-krotki") is None


def test_device_label() -> None:
    assert device_label(PHONE_UA) == "Chrome · Android"
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Safari/604.1"
    assert device_label(iphone) == "Safari · iOS"
    assert device_label(None) == "Przeglądarka"


def test_store_recovery_codes_single_use(conn: sqlite3.Connection) -> None:
    store = AccountStore(conn)
    user = store.create("a@example.com", GOOD)
    codes = store.create_recovery_codes(user.id)
    assert store.recovery_codes_left(user.id) == 10
    assert store.use_recovery_code(user.id, codes[0])
    assert not store.use_recovery_code(user.id, codes[0])
    assert store.recovery_codes_left(user.id) == 9
    new = store.create_recovery_codes(user.id)
    assert not store.use_recovery_code(user.id, codes[1])  # stary zestaw nie działa
    assert store.use_recovery_code(user.id, new[1])


def test_store_recover_locks_after_many_failures(conn: sqlite3.Connection) -> None:
    store = AccountStore(conn, max_failed_logins=3)
    user = store.create("a@example.com", GOOD)
    codes = store.create_recovery_codes(user.id)
    for _ in range(3):
        with pytest.raises(AccountError):
            store.recover("a@example.com", "AAAA-BBBB-CCCC-DDDD")
    with pytest.raises(AccountError, match="Za dużo"):
        store.recover("a@example.com", codes[0])  # zablokowane, nawet dobrym kodem
    with pytest.raises(AccountError, match="Nieprawidłowy"):
        store.recover("nie-ma@example.com", codes[0])


def test_short_session_ttl(conn: sqlite3.Connection) -> None:
    now = dt.datetime(2026, 10, 24, 12, tzinfo=dt.UTC)
    store = AccountStore(conn, now=lambda: now)
    user = store.create("a@example.com", GOOD)
    assert store.create_session(user.id).expires_at == now + dt.timedelta(days=30)
    short = store.create_session(user.id, persistent=False)
    assert short.expires_at == now + dt.timedelta(hours=12)


# ---------------------------------------------------------------------- API


def test_register_returns_recovery_codes_once(nomail: TestClient) -> None:
    body = register(nomail)
    codes = body["recovery_codes"]
    assert isinstance(codes, list) and len(codes) == 10
    assert "recovery_codes" not in nomail.get("/api/auth/me").json()
    sec = nomail.get("/api/account/security").json()
    assert sec == {"two_factor": False, "recovery_codes_left": 10}


def test_recover_without_mail(nomail: TestClient) -> None:
    codes = register(nomail)["recovery_codes"]
    assert isinstance(codes, list)
    logout(nomail)
    # słabe hasło nie zużywa kodu
    r = nomail.post(
        "/api/auth/recover", json={"email": "jan@example.com", "code": codes[0], "password": "x"}
    )
    assert r.status_code == 400
    r = nomail.post(
        "/api/auth/recover",
        json={"email": "jan@example.com", "code": "AAAA-BBBB-CCCC-DDDD", "password": NEW},
    )
    assert r.status_code == 401
    assert "Nieprawidłowy e-mail lub kod" in r.json()["detail"]
    r = nomail.post(
        "/api/auth/recover",
        json={"email": "JAN@example.com", "code": codes[0].lower(), "password": NEW},
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == "jan@example.com"
    assert r.json()["recovery_codes_left"] == 9
    logout(nomail)
    assert (
        nomail.post("/api/auth/login", json={"email": "jan@example.com", "password": NEW})
    ).status_code == 200
    # ten sam kod drugi raz nie działa
    logout(nomail)
    r = nomail.post(
        "/api/auth/recover", json={"email": "jan@example.com", "code": codes[0], "password": GOOD}
    )
    assert r.status_code == 401


def test_two_factor_login_flow(nomail: TestClient) -> None:
    codes = register(nomail)["recovery_codes"]
    assert isinstance(codes, list)
    secret = enable_2fa(nomail)
    assert nomail.get("/api/auth/me").json()["user"]["two_factor"] is True
    logout(nomail)

    r = nomail.post("/api/auth/login", json={"email": "jan@example.com", "password": GOOD})
    assert r.status_code == 200
    body = r.json()
    assert body["mfa_required"] is True and body["user"] is None
    assert nomail.get("/api/auth/me").json()["user"] is None  # bez sesji przed drugim krokiem
    challenge = body["challenge"]

    bad = nomail.post("/api/auth/login/2fa", json={"challenge": challenge, "code": "000000"})
    assert bad.status_code == 401
    # kod użyty przy włączaniu nie zadziała drugi raz (ochrona przed powtórką), więc
    # bierzemy kod odzyskiwania
    ok = nomail.post("/api/auth/login/2fa", json={"challenge": challenge, "code": codes[0]})
    assert ok.status_code == 200, ok.text
    assert ok.json()["user"]["email"] == "jan@example.com"
    # wyzwanie jest jednorazowe
    again = nomail.post("/api/auth/login/2fa", json={"challenge": challenge, "code": codes[1]})
    assert again.status_code == 401
    assert secret


def test_two_factor_challenge_dies_after_attempts(nomail: TestClient) -> None:
    register(nomail)
    enable_2fa(nomail)
    logout(nomail)
    challenge = nomail.post(
        "/api/auth/login", json={"email": "jan@example.com", "password": GOOD}
    ).json()["challenge"]
    for _ in range(5):
        nomail.post("/api/auth/login/2fa", json={"challenge": challenge, "code": "111111"})
    r = nomail.post("/api/auth/login/2fa", json={"challenge": challenge, "code": "111111"})
    assert "wygasło" in r.json()["detail"]


def test_recover_does_not_bypass_two_factor(nomail: TestClient) -> None:
    codes = register(nomail)["recovery_codes"]
    assert isinstance(codes, list)
    enable_2fa(nomail)
    logout(nomail)
    r = nomail.post(
        "/api/auth/recover", json={"email": "jan@example.com", "code": codes[0], "password": NEW}
    )
    assert r.status_code == 200
    assert r.json()["mfa_required"] is True
    assert nomail.get("/api/auth/me").json()["user"] is None
    ok = nomail.post(
        "/api/auth/login/2fa", json={"challenge": r.json()["challenge"], "code": codes[1]}
    )
    assert ok.status_code == 200


def test_email_reset_does_not_bypass_two_factor(mail: TestClient, outbox: Outbox) -> None:
    register(mail)
    enable_2fa(mail)
    logout(mail)
    mail.post("/api/auth/request-reset", json={"email": "jan@example.com"})
    r = mail.post("/api/auth/reset", json={"token": outbox.link_token(), "password": NEW})
    assert r.status_code == 200
    assert r.json()["mfa_required"] is True


def test_disable_two_factor(nomail: TestClient) -> None:
    codes = register(nomail)["recovery_codes"]
    assert isinstance(codes, list)
    enable_2fa(nomail)
    h = csrf(nomail)
    r = nomail.post("/api/account/2fa/disable", json={"password": GOOD, "code": "123"}, headers=h)
    assert r.status_code == 400
    r = nomail.post(
        "/api/account/2fa/disable", json={"password": GOOD, "code": codes[2]}, headers=h
    )
    assert r.status_code == 200
    logout(nomail)
    r = nomail.post("/api/auth/login", json={"email": "jan@example.com", "password": GOOD})
    assert r.json()["user"]["email"] == "jan@example.com"


def test_setup_requires_password(nomail: TestClient) -> None:
    register(nomail)
    r = nomail.post("/api/account/2fa/setup", json={"password": "zle-haslo"}, headers=csrf(nomail))
    assert r.status_code == 400
    r = nomail.post("/api/account/2fa/enable", json={"code": "123456"}, headers=csrf(nomail))
    assert r.status_code == 400


def test_regenerate_recovery_codes(nomail: TestClient) -> None:
    old = register(nomail)["recovery_codes"]
    assert isinstance(old, list)
    r = nomail.post("/api/account/recovery-codes", json={"password": GOOD}, headers=csrf(nomail))
    new = r.json()["recovery_codes"]
    assert len(new) == 10 and not set(new) & set(old)


def test_sessions_list_and_revoke(tmp_path: Path) -> None:
    with make_client(tmp_path, None) as phone, make_client(tmp_path, None) as laptop:
        register(phone)
        laptop.post(
            "/api/auth/login",
            json={"email": "jan@example.com", "password": GOOD},
            headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0) Firefox/130.0"},
        )
        sessions = phone.get("/api/account/sessions").json()
        assert len(sessions) == 2
        mine = next(s for s in sessions if s["current"])
        other = next(s for s in sessions if not s["current"])
        assert mine["device"] == "Chrome · Android"
        assert other["device"] == "Firefox · Windows"
        r = phone.delete(f"/api/account/sessions/{other['id']}", headers=csrf(phone))
        assert r.status_code == 204
        assert laptop.get("/api/auth/me").json()["user"] is None
        assert phone.delete("/api/account/sessions/nie-ma", headers=csrf(phone)).status_code == 404


def test_remember_me_controls_cookie(nomail: TestClient) -> None:
    register(nomail)
    logout(nomail)
    r = nomail.post(
        "/api/auth/login",
        json={"email": "jan@example.com", "password": GOOD, "remember": False},
    )
    cookie = r.headers["set-cookie"]
    assert "termin_session=" in cookie and "Max-Age" not in cookie
    logout(nomail)
    r = nomail.post("/api/auth/login", json={"email": "jan@example.com", "password": GOOD})
    assert "Max-Age" in r.headers["set-cookie"]


def test_change_email(mail: TestClient, outbox: Outbox) -> None:
    register(mail)
    mail.post("/api/account/email", json={"email": "x", "password": GOOD}, headers=csrf(mail))
    r = mail.post(
        "/api/account/email",
        json={"email": "nowy@example.com", "password": "zle"},
        headers=csrf(mail),
    )
    assert r.status_code == 400
    r = mail.post(
        "/api/account/email",
        json={"email": "Nowy@Example.com", "password": GOOD},
        headers=csrf(mail),
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"] == {**r.json()["user"], "email": "nowy@example.com", "verified": False}
    recipients = [m[0] for m in outbox.mails]
    assert "jan@example.com" in recipients[-2:]  # informacja na stary adres
    assert recipients[-1] == "nowy@example.com"  # link potwierdzający na nowy
    with make_client(Path(str(mail.app.state.runtime.settings.data_dir)), None) as other:  # type: ignore[attr-defined]
        register(other, "drugi@example.com")
        r = other.post(
            "/api/account/email",
            json={"email": "nowy@example.com", "password": GOOD},
            headers=csrf(other),
        )
        assert r.status_code == 409


def test_password_change_sends_notice(mail: TestClient, outbox: Outbox) -> None:
    register(mail)
    r = mail.post("/api/account/password", json={"current": GOOD, "new": NEW}, headers=csrf(mail))
    assert r.status_code == 200
    assert "hasło zmienione" in outbox.mails[-1][1]


def test_export_contains_security_fields(nomail: TestClient) -> None:
    register(nomail)
    data = nomail.get("/api/account/export").json()
    assert data["account"]["recovery_codes_left"] == 10
    assert data["account"]["two_factor"] is False
    assert data["sessions"][0]["device"] == "Chrome · Android"
