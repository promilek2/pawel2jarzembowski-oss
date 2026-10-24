"""Konta użytkowników, sesje i tokeny e-mail (ADR-0010).

Konto jest opcjonalne. Przechowujemy minimum: adres e-mail, skrót hasła, daty akceptacji
regulaminu i weryfikacji. Tokeny sesji i e-mail zapisujemy wyłącznie jako skrót SHA-256.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from termin.security import (
    DUMMY_HASH,
    hash_password,
    hash_password_async,
    new_token,
    token_hash,
    verify_password,
    verify_password_async,
)

TERMS_VERSION = "2026-10-25"
TokenPurpose = Literal["verify", "reset"]
TOKEN_TTL = {"verify": dt.timedelta(days=3), "reset": dt.timedelta(hours=1)}


class AccountError(ValueError):
    """Komunikat dla użytkownika."""


class LockedError(AccountError):
    pass


@dataclass(frozen=True, slots=True)
class User:
    id: int
    email: str
    created_at: dt.datetime
    email_verified_at: dt.datetime | None
    terms_accepted_at: dt.datetime
    terms_version: str
    last_login_at: dt.datetime | None

    @property
    def verified(self) -> bool:
        return self.email_verified_at is not None

    def public_dict(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "verified": self.verified,
            "created_at": self.created_at.isoformat(),
            "terms_version": self.terms_version,
        }


@dataclass(frozen=True, slots=True)
class Session:
    token: str
    """Surowy token (tylko w chwili utworzenia; w bazie jest skrót)."""
    csrf_token: str
    user_id: int
    expires_at: dt.datetime


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


class AccountStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
        max_failed_logins: int = 10,
        lockout: dt.timedelta = dt.timedelta(minutes=15),
        session_ttl: dt.timedelta = dt.timedelta(days=30),
    ) -> None:
        self.conn = conn
        self._now = now
        self.max_failed_logins = max_failed_logins
        self.lockout = lockout
        self.session_ttl = session_ttl

    # ------------------------------------------------------------------ użytkownicy

    def _user(self, row: sqlite3.Row | None) -> User | None:
        if row is None:
            return None
        return User(
            id=row["id"],
            email=row["email"],
            created_at=dt.datetime.fromisoformat(row["created_at"]),
            email_verified_at=_dt(row["email_verified_at"]),
            terms_accepted_at=dt.datetime.fromisoformat(row["terms_accepted_at"]),
            terms_version=row["terms_version"],
            last_login_at=_dt(row["last_login_at"]),
        )

    def get(self, user_id: int) -> User | None:
        return self._user(
            self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        )

    def by_email(self, email: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return self._user(row)

    def create(self, email: str, password: str, *, verified: bool = False) -> User:
        return self._insert(email, hash_password(password), verified=verified)

    async def create_async(self, email: str, password: str) -> User:
        """Jak `create`, ale scrypt liczony w puli wątków (nie blokuje serwera)."""
        if self.by_email(email) is not None:
            raise AccountError("Konto z tym adresem e-mail już istnieje.")
        return self._insert(email, await hash_password_async(password), verified=False)

    def _insert(self, email: str, password_hash: str, *, verified: bool) -> User:
        now = self._now()
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO users (email, password_hash, created_at, email_verified_at,"
                    " terms_accepted_at, terms_version) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        email,
                        password_hash,
                        now.isoformat(),
                        now.isoformat() if verified else None,
                        now.isoformat(),
                        TERMS_VERSION,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise AccountError("Konto z tym adresem e-mail już istnieje.") from exc
        user = self.get(int(cur.lastrowid or 0))
        assert user is not None
        return user

    def authenticate(self, email: str, password: str) -> User:
        """Sprawdza hasło z blokadą po wielu nieudanych próbach. Czas odpowiedzi stały."""
        row = self._login_row(email)
        ok = verify_password(password, row["password_hash"] if row else DUMMY_HASH)
        return self._finish_login(row, ok)

    async def authenticate_async(self, email: str, password: str) -> User:
        row = self._login_row(email)
        ok = await verify_password_async(password, row["password_hash"] if row else DUMMY_HASH)
        return self._finish_login(row, ok)

    def _login_row(self, email: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        if row is not None:
            locked_until = _dt(row["locked_until"])
            if locked_until and locked_until > self._now():
                raise LockedError(
                    "Za dużo nieudanych prób logowania. Spróbuj ponownie za kilkanaście minut."
                )
        return row

    def _finish_login(self, row: sqlite3.Row | None, ok: bool) -> User:
        if row is None:
            raise AccountError("Nieprawidłowy e-mail lub hasło.")
        now = self._now()
        if not ok:
            failed = row["failed_logins"] + 1
            lock = now + self.lockout if failed >= self.max_failed_logins else None
            with self.conn:
                self.conn.execute(
                    "UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                    (0 if lock else failed, _iso(lock), row["id"]),
                )
            raise AccountError("Nieprawidłowy e-mail lub hasło.")
        with self.conn:
            self.conn.execute(
                "UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = ?"
                " WHERE id = ?",
                (now.isoformat(), row["id"]),
            )
        user = self.get(row["id"])
        assert user is not None
        return user

    def set_password(self, user_id: int, password: str) -> None:
        self._store_password(user_id, hash_password(password))

    async def set_password_async(self, user_id: int, password: str) -> None:
        self._store_password(user_id, await hash_password_async(password))

    def _store_password(self, user_id: int, password_hash: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE users SET password_hash = ?, failed_logins = 0, locked_until = NULL"
                " WHERE id = ?",
                (password_hash, user_id),
            )
            # zmiana hasła wylogowuje wszystkie urządzenia
            self.conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def check_password(self, user_id: int, password: str) -> bool:
        row = self.conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return bool(row) and verify_password(password, row[0])

    async def check_password_async(self, user_id: int, password: str) -> bool:
        row = self.conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return bool(row) and await verify_password_async(password, row[0])

    def mark_verified(self, user_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?) WHERE id = ?",
                (self._now().isoformat(), user_id),
            )

    def delete(self, user_id: int) -> None:
        """Usuwa konto razem z sesjami, tokenami i obserwacjami (ON DELETE CASCADE)."""
        with self.conn:
            self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def export(self, user_id: int) -> dict[str, Any]:
        """Wszystkie dane konta (art. 15 i 20 RODO)."""
        user = self.get(user_id)
        if user is None:
            raise AccountError("Nie ma takiego konta.")
        subs = self.conn.execute(
            "SELECT token, created_at, label, benefit_query, variant, case_no, province, lat, lon,"
            " radius_km, area_label, before_date, channel, target, last_notified_date,"
            " consent_at FROM subscriptions WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ).fetchall()
        sessions = self.conn.execute(
            "SELECT created_at, expires_at, last_seen_at FROM sessions WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {
            "account": {
                **user.public_dict(),
                "email_verified_at": _iso(user.email_verified_at),
                "terms_accepted_at": user.terms_accepted_at.isoformat(),
                "last_login_at": _iso(user.last_login_at),
            },
            "subscriptions": [dict(r) for r in subs],
            "sessions": [dict(r) for r in sessions],
            "exported_at": self._now().isoformat(),
        }

    # ------------------------------------------------------------------ sesje

    def create_session(self, user_id: int) -> Session:
        token, csrf = new_token(), new_token(24)
        now = self._now()
        expires = now + self.session_ttl
        with self.conn:
            self.conn.execute(
                "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at,"
                " last_seen_at) VALUES (?, ?, ?, ?, ?, ?)",
                (token_hash(token), user_id, csrf, now.isoformat(), expires.isoformat(),
                 now.isoformat()),
            )  # fmt: skip
        return Session(token=token, csrf_token=csrf, user_id=user_id, expires_at=expires)

    def session_user(self, token: str) -> tuple[User, str] | None:
        """(użytkownik, token CSRF) dla ważnej sesji."""
        row = self.conn.execute(
            "SELECT user_id, csrf_token, expires_at, last_seen_at FROM sessions"
            " WHERE token_hash = ?",
            (token_hash(token),),
        ).fetchone()
        now = self._now()
        if row is None or dt.datetime.fromisoformat(row["expires_at"]) <= now:
            return None
        if now - dt.datetime.fromisoformat(row["last_seen_at"]) > dt.timedelta(minutes=10):
            with self.conn:
                self.conn.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (now.isoformat(), token_hash(token)),
                )
        user = self.get(row["user_id"])
        return (user, row["csrf_token"]) if user else None

    def delete_session(self, token: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))

    def delete_sessions(self, user_id: int) -> int:
        with self.conn:
            return self.conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,)).rowcount

    # ------------------------------------------------------------------ tokeny e-mail

    def create_email_token(self, user_id: int, purpose: TokenPurpose) -> str:
        token = new_token()
        now = self._now()
        with self.conn:
            # nowy token unieważnia poprzednie tego samego rodzaju
            self.conn.execute(
                "DELETE FROM email_tokens WHERE user_id = ? AND purpose = ?", (user_id, purpose)
            )
            self.conn.execute(
                "INSERT INTO email_tokens (token_hash, user_id, purpose, created_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (token_hash(token), user_id, purpose, now.isoformat(),
                 (now + TOKEN_TTL[purpose]).isoformat()),
            )  # fmt: skip
        return token

    def use_email_token(self, token: str, purpose: TokenPurpose, *, consume: bool = True) -> int:
        """Zwraca id użytkownika i (domyślnie) zużywa token. Rzuca AccountError, gdy nieważny."""
        row = self.conn.execute(
            "SELECT user_id, expires_at, used_at FROM email_tokens"
            " WHERE token_hash = ? AND purpose = ?",
            (token_hash(token), purpose),
        ).fetchone()
        now = self._now()
        if row is None or row["used_at"] or dt.datetime.fromisoformat(row["expires_at"]) <= now:
            raise AccountError("Link wygasł albo został już użyty. Poproś o nowy.")
        if not consume:
            return int(row["user_id"])
        with self.conn:
            self.conn.execute(
                "UPDATE email_tokens SET used_at = ? WHERE token_hash = ?",
                (now.isoformat(), token_hash(token)),
            )
        return int(row["user_id"])

    # ------------------------------------------------------------------ porządki

    def purge(self, *, unverified_after: dt.timedelta | None = None) -> dict[str, int]:
        """Usuwa wygasłe sesje i tokeny oraz (opcjonalnie) stare niepotwierdzone konta."""
        now = self._now().isoformat()
        out: dict[str, int] = {}
        with self.conn:
            out["sessions"] = self.conn.execute(
                "DELETE FROM sessions WHERE expires_at <= ?", (now,)
            ).rowcount
            out["email_tokens"] = self.conn.execute(
                "DELETE FROM email_tokens WHERE expires_at <= ? OR used_at IS NOT NULL", (now,)
            ).rowcount
            if unverified_after is not None:
                cutoff = (self._now() - unverified_after).isoformat()
                out["unverified_users"] = self.conn.execute(
                    "DELETE FROM users WHERE email_verified_at IS NULL AND created_at <= ?",
                    (cutoff,),
                ).rowcount
        return out
