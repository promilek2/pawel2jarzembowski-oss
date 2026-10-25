"""Subskrypcje Web Push (urządzenia zalogowanych użytkowników)."""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

MAX_DEVICES_PER_USER = 10


@dataclass(frozen=True, slots=True)
class PushSubscription:
    id: int
    user_id: int
    endpoint: str
    p256dh: str
    auth: str
    device: str | None


class PushStore:
    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        self.conn = conn
        self._now = now

    def add(self, user_id: int, endpoint: str, p256dh: str, auth: str, device: str | None) -> None:
        """Zapisuje urządzenie. Ten sam endpoint z innego konta przechodzi na nowe konto."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, device,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (endpoint) DO UPDATE SET"
                " user_id = excluded.user_id, p256dh = excluded.p256dh, auth = excluded.auth,"
                " device = excluded.device",
                (user_id, endpoint, p256dh, auth, device, self._now().isoformat()),
            )
            # najstarsze urządzenia ponad limit wypadają
            self.conn.execute(
                "DELETE FROM push_subscriptions WHERE user_id = ? AND id NOT IN ("
                " SELECT id FROM push_subscriptions WHERE user_id = ?"
                " ORDER BY created_at DESC, id DESC LIMIT ?)",
                (user_id, user_id, MAX_DEVICES_PER_USER),
            )

    def for_user(self, user_id: int) -> list[PushSubscription]:
        rows = self.conn.execute(
            "SELECT id, user_id, endpoint, p256dh, auth, device FROM push_subscriptions"
            " WHERE user_id = ? ORDER BY id",
            (user_id,),
        ).fetchall()
        return [PushSubscription(**dict(r)) for r in rows]

    def count(self, user_id: int) -> int:
        row = self.conn.execute(
            "SELECT count(*) FROM push_subscriptions WHERE user_id = ?", (user_id,)
        ).fetchone()
        return int(row[0])

    def remove(self, user_id: int, endpoint: str) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?",
                (user_id, endpoint),
            )
        return cur.rowcount > 0

    def drop(self, subscription_id: int) -> None:
        """Urządzenie wygasło (usługa push odpowiedziała 404/410)."""
        with self.conn:
            self.conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (subscription_id,))

    def mark_ok(self, subscription_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE push_subscriptions SET last_ok_at = ? WHERE id = ?",
                (self._now().isoformat(), subscription_id),
            )
