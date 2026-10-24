from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterator

import pytest

from termin.security import (
    hash_password,
    normalize_email,
    password_problem,
    token_hash,
    verify_password,
)
from termin.store.accounts import AccountError, AccountStore, LockedError
from termin.store.db import connect

GOOD = "Kolejka-do-okulisty-7"


class Clock:
    def __init__(self) -> None:
        self.now = dt.datetime(2026, 10, 24, 12, tzinfo=dt.UTC)

    def __call__(self) -> dt.datetime:
        return self.now


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(conn: sqlite3.Connection, clock: Clock) -> AccountStore:
    return AccountStore(conn, now=clock, max_failed_logins=3, lockout=dt.timedelta(minutes=15))


def test_password_hashing() -> None:
    h = hash_password(GOOD)
    assert h.startswith("scrypt$")
    assert GOOD not in h
    assert verify_password(GOOD, h)
    assert not verify_password("inne-haslo-123", h)
    assert not verify_password(GOOD, "bcrypt$zly")
    assert not verify_password(GOOD, "śmieci")
    assert hash_password(GOOD) != h  # sól


@pytest.mark.parametrize(
    ("password", "ok"),
    [
        ("krotkie", False),
        ("1234567890", False),
        ("aaaaaaaaaaaa", False),
        (GOOD, True),
        ("x" * 129, False),
    ],
)
def test_password_policy(password: str, ok: bool) -> None:
    assert (password_problem(password) is None) is ok


def test_password_must_not_contain_email_name() -> None:
    assert password_problem("janekkowalski99!", "janekkowalski@example.com") is not None


def test_normalize_email() -> None:
    assert normalize_email(" Jan.Kowalski@Example.COM ") == "jan.kowalski@example.com"
    assert normalize_email("zly@") is None
    assert normalize_email("a@b") is None
    assert normalize_email("<script>@x.pl") is None


def test_create_and_authenticate(store: AccountStore) -> None:
    user = store.create("jan@example.com", GOOD)
    assert not user.verified
    assert store.authenticate("jan@example.com", GOOD).id == user.id
    with pytest.raises(AccountError, match="Nieprawidłowy"):
        store.authenticate("jan@example.com", "zle-haslo-1234")
    with pytest.raises(AccountError, match="Nieprawidłowy"):
        store.authenticate("nikt@example.com", GOOD)  # ten sam komunikat
    with pytest.raises(AccountError, match="istnieje"):
        store.create("jan@example.com", GOOD)


def test_lockout_after_failed_logins(store: AccountStore, clock: Clock) -> None:
    store.create("jan@example.com", GOOD)
    for _ in range(3):
        with pytest.raises(AccountError):
            store.authenticate("jan@example.com", "zle-haslo-1234")
    with pytest.raises(LockedError):
        store.authenticate("jan@example.com", GOOD)  # nawet dobre hasło jest zablokowane
    clock.now += dt.timedelta(minutes=16)
    assert store.authenticate("jan@example.com", GOOD)


def test_sessions(store: AccountStore, conn: sqlite3.Connection, clock: Clock) -> None:
    user = store.create("jan@example.com", GOOD)
    session = store.create_session(user.id)
    stored = conn.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert stored == token_hash(session.token)
    assert session.token not in stored  # w bazie tylko skrót
    found = store.session_user(session.token)
    assert found is not None
    assert found[0].id == user.id
    assert found[1] == session.csrf_token
    assert store.session_user("nie-ma") is None
    clock.now += dt.timedelta(days=31)
    assert store.session_user(session.token) is None
    assert store.purge()["sessions"] == 1


def test_password_change_logs_out_everywhere(store: AccountStore) -> None:
    user = store.create("jan@example.com", GOOD)
    s1, s2 = store.create_session(user.id), store.create_session(user.id)
    store.set_password(user.id, "Nowe-haslo-do-konta-9")
    assert store.session_user(s1.token) is None
    assert store.session_user(s2.token) is None
    assert store.check_password(user.id, "Nowe-haslo-do-konta-9")


def test_email_tokens(store: AccountStore, clock: Clock) -> None:
    user = store.create("jan@example.com", GOOD)
    old = store.create_email_token(user.id, "verify")
    token = store.create_email_token(user.id, "verify")
    with pytest.raises(AccountError):
        store.use_email_token(old, "verify")  # nowy token unieważnia stary
    with pytest.raises(AccountError):
        store.use_email_token(token, "reset")  # inny cel
    assert store.use_email_token(token, "verify") == user.id
    with pytest.raises(AccountError, match="użyty"):
        store.use_email_token(token, "verify")  # jednorazowy
    reset = store.create_email_token(user.id, "reset")
    clock.now += dt.timedelta(hours=2)
    with pytest.raises(AccountError, match="wygasł"):
        store.use_email_token(reset, "reset")


def test_delete_cascades_and_export(store: AccountStore, conn: sqlite3.Connection) -> None:
    user = store.create("jan@example.com", GOOD)
    store.create_session(user.id)
    conn.execute(
        "INSERT INTO subscriptions (token, created_at, label, benefit_query, channel, target,"
        " province, user_id) VALUES ('t', '2026-10-24', 'Okulista', 'X', 'ntfy', 'x', '11', ?)",
        (user.id,),
    )
    data = store.export(user.id)
    assert data["account"]["email"] == "jan@example.com"
    assert "password_hash" not in str(data)
    assert data["subscriptions"][0]["label"] == "Okulista"
    store.delete(user.id)
    for table in ("users", "sessions", "subscriptions"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    with pytest.raises(AccountError):
        store.export(user.id)


def test_purge_unverified(store: AccountStore, clock: Clock) -> None:
    store.create("stary@example.com", GOOD)
    verified = store.create("ok@example.com", GOOD, verified=True)
    clock.now += dt.timedelta(days=8)
    out = store.purge(unverified_after=dt.timedelta(days=7))
    assert out["unverified_users"] == 1
    assert store.get(verified.id) is not None
