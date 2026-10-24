"""Subskrypcje powiadomień: bez kont, z losowym tokenem, z prostym usuwaniem (PRIVACY.md)."""

from __future__ import annotations

import datetime as dt
import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from termin.core.benefits import Variant

CHANNELS = ("ntfy", "telegram", "email", "desktop")
COORD_DECIMALS = 2
"""Zaokrąglenie współrzędnych (~1 km): dokładniejsza lokalizacja nie jest potrzebna."""


@dataclass(frozen=True, slots=True)
class Subscription:
    token: str
    label: str
    benefit_query: str
    channel: str
    target: str
    variant: Variant = Variant.ANY
    exclude: tuple[str, ...] = ()
    case_no: int = 1
    province: str | None = None
    lat: float | None = None
    lon: float | None = None
    radius_km: float | None = None
    area_label: str | None = None
    before_date: dt.date | None = None
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    last_notified_date: dt.date | None = None
    last_notified_queue: str | None = None
    last_notified_at: dt.datetime | None = None
    last_checked_at: dt.datetime | None = None
    active: bool = True
    user_id: int | None = None
    consent_at: dt.datetime | None = None
    """Wyraźna zgoda na przetwarzanie informacji o świadczeniu (art. 9 ust. 2 lit. a RODO)."""

    @property
    def has_point(self) -> bool:
        return self.lat is not None and self.lon is not None

    def public_dict(self) -> dict[str, Any]:
        """Bez celu powiadomień (adres e-mail, czat): bezpieczne do pokazania w UI."""
        return {
            "token": self.token,
            "label": self.label,
            "channel": self.channel,
            "case": self.case_no,
            "province": self.province,
            "radius_km": self.radius_km,
            "area_label": self.area_label,
            "before_date": self.before_date.isoformat() if self.before_date else None,
            "last_notified_date": (
                self.last_notified_date.isoformat() if self.last_notified_date else None
            ),
            "created_at": self.created_at.isoformat(),
            "consent_at": self.consent_at.isoformat() if self.consent_at else None,
        }


def new_token() -> str:
    return secrets.token_urlsafe(16)


def _dt(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def _d(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value) if value else None


def _round(value: float | None) -> float | None:
    return round(value, COORD_DECIMALS) if value is not None else None


class SubscriptionStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def add(self, sub: Subscription) -> Subscription:
        if sub.channel not in CHANNELS:
            raise ValueError(f"Nieznany kanał: {sub.channel}")
        if sub.province is None and not sub.has_point:
            raise ValueError("Subskrypcja wymaga województwa albo punktu z promieniem")
        with self.conn:
            self.conn.execute(
                "INSERT INTO subscriptions (token, created_at, label, benefit_query, variant,"
                " exclude, case_no, province, lat, lon, radius_km, area_label, before_date,"
                " channel, target, active, user_id, consent_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    sub.token,
                    sub.created_at.isoformat(),
                    sub.label,
                    sub.benefit_query,
                    sub.variant.value,
                    json.dumps(list(sub.exclude), ensure_ascii=False),
                    sub.case_no,
                    sub.province,
                    _round(sub.lat),
                    _round(sub.lon),
                    sub.radius_km,
                    sub.area_label,
                    sub.before_date.isoformat() if sub.before_date else None,
                    sub.channel,
                    sub.target,
                    sub.user_id,
                    sub.consent_at.isoformat() if sub.consent_at else None,
                ),
            )
        stored = self.get(sub.token)
        assert stored is not None
        return stored

    def _from_row(self, row: sqlite3.Row) -> Subscription:
        return Subscription(
            token=row["token"],
            label=row["label"],
            benefit_query=row["benefit_query"],
            channel=row["channel"],
            target=row["target"],
            variant=Variant(row["variant"]),
            exclude=tuple(json.loads(row["exclude"] or "[]")),
            case_no=row["case_no"],
            province=row["province"],
            lat=row["lat"],
            lon=row["lon"],
            radius_km=row["radius_km"],
            area_label=row["area_label"],
            before_date=_d(row["before_date"]),
            created_at=_dt(row["created_at"]) or dt.datetime.now(dt.UTC),
            last_notified_date=_d(row["last_notified_date"]),
            last_notified_queue=row["last_notified_queue"],
            last_notified_at=_dt(row["last_notified_at"]),
            last_checked_at=_dt(row["last_checked_at"]),
            active=bool(row["active"]),
            user_id=row["user_id"],
            consent_at=_dt(row["consent_at"]),
        )

    def get(self, token: str) -> Subscription | None:
        row = self.conn.execute("SELECT * FROM subscriptions WHERE token = ?", (token,)).fetchone()
        return self._from_row(row) if row else None

    def active(self) -> list[Subscription]:
        rows = self.conn.execute(
            "SELECT * FROM subscriptions WHERE active = 1 ORDER BY created_at"
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def for_target(self, channel: str, target: str) -> list[Subscription]:
        rows = self.conn.execute(
            "SELECT * FROM subscriptions WHERE channel = ? AND target = ? ORDER BY created_at",
            (channel, target),
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def for_user(self, user_id: int) -> list[Subscription]:
        rows = self.conn.execute(
            "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at", (user_id,)
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def count(
        self, *, channel: str | None = None, target: str | None = None, user_id: int | None = None
    ) -> int:
        sql = "SELECT count(*) FROM subscriptions WHERE active = 1"
        params: list[str | int] = []
        if channel is not None and target is not None:
            sql += " AND channel = ? AND target = ?"
            params += [channel, target]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        return int(self.conn.execute(sql, params).fetchone()[0])

    def mark_checked(self, token: str, when: dt.datetime) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE subscriptions SET last_checked_at = ? WHERE token = ?",
                (when.isoformat(), token),
            )

    def mark_notified(
        self, token: str, first_date: dt.date, queue_id: str, when: dt.datetime
    ) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE subscriptions SET last_notified_date = ?, last_notified_queue = ?,"
                " last_notified_at = ? WHERE token = ?",
                (first_date.isoformat(), queue_id, when.isoformat(), token),
            )

    def delete(self, token: str) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM subscriptions WHERE token = ?", (token,))
        return cur.rowcount > 0

    def delete_for_target(self, channel: str, target: str) -> int:
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM subscriptions WHERE channel = ? AND target = ?", (channel, target)
            )
        return cur.rowcount

    def delete_all(self) -> int:
        with self.conn:
            cur = self.conn.execute("DELETE FROM subscriptions")
        return cur.rowcount


def wipe_user_data(conn: sqlite3.Connection, *, include_history: bool = False) -> dict[str, int]:
    """Usuwa wszystkie dane pochodzące od użytkowników (konta, sesje, subskrypcje, geokody).

    `include_history=True` usuwa też snapshoty (publiczne dane NFZ) i cache odpowiedzi NFZ.
    """
    counts: dict[str, int] = {}
    tables = ["subscriptions", "geocode_cache", "email_tokens", "sessions", "users"]
    if include_history:
        tables += ["snapshots", "http_cache"]
    with conn:
        for table in tables:
            counts[table] = conn.execute(f"DELETE FROM {table}").rowcount  # noqa: S608 - stała lista
    conn.execute("VACUUM")
    return counts
