"""Dane zalogowanych użytkowników (ADR-0012): zgłoszenia z telefonu, sprawy, notatki.

Wszystko to może być informacją o zdrowiu (art. 9 RODO), więc zapis wymaga zgody
(`users.health_consent_at`), a jej wycofanie usuwa dane (`withdraw_consent`).
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

QUEUE_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
REPORT_WINDOW_DAYS = 14
"""Z ilu dni pokazujemy zgłoszenia przy wyniku (starsze mówią mało o dzisiejszej kolejce)."""
MAX_REPORTS_PER_DAY = 30
MAX_CASES = 200
NOTE_MAX = 1000
Outcome = Literal["same", "other", "none"]
CaseStatus = Literal["to_call", "waiting", "booked", "done", "rejected"]
CASE_STATUSES: tuple[str, ...] = ("to_call", "waiting", "booked", "done", "rejected")


class PersonalDataError(ValueError):
    """Komunikat dla użytkownika."""


def valid_queue_id(queue_id: str) -> bool:
    return bool(QUEUE_ID.match(queue_id))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


# ---------------------------------------------------------------------- zgoda


def has_consent(conn: sqlite3.Connection, user_id: int) -> bool:
    row = conn.execute("SELECT health_consent_at FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row and row["health_consent_at"])


def give_consent(conn: sqlite3.Connection, user_id: int, now: dt.datetime | None = None) -> None:
    with conn:
        conn.execute(
            "UPDATE users SET health_consent_at = COALESCE(health_consent_at, ?) WHERE id = ?",
            ((now or _now()).isoformat(), user_id),
        )


def withdraw_consent(conn: sqlite3.Connection, user_id: int) -> dict[str, int]:
    """Wycofanie zgody usuwa sprawy, notatki i zgłoszenia (obserwacje mają własną zgodę)."""
    with conn:
        out = {
            table: conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,)).rowcount  # noqa: S608
            for table in ("cases", "facility_notes", "phone_reports")
        }
        conn.execute("UPDATE users SET health_consent_at = NULL WHERE id = ?", (user_id,))
    return out


# ---------------------------------------------------------------------- zgłoszenia z telefonu


@dataclass(frozen=True, slots=True)
class PhoneReport:
    queue_id: str
    reached: bool
    outcome: Outcome | None = None
    nfz_date: dt.date | None = None
    offered_date: dt.date | None = None


class PhoneReportStore:
    def __init__(self, conn: sqlite3.Connection, *, now: Callable[[], dt.datetime] = _now) -> None:
        self.conn = conn
        self._now = now

    def add(self, user_id: int, report: PhoneReport, today: dt.date) -> None:
        """Zapisuje (albo poprawia dzisiejsze) zgłoszenie użytkownika dla kolejki."""
        if not valid_queue_id(report.queue_id):
            raise PersonalDataError("Nieznana placówka.")
        if report.reached and report.outcome is None:
            raise PersonalDataError("Zaznacz, jaki termin zaproponowano.")
        if report.offered_date is not None and not (
            today - dt.timedelta(days=1) <= report.offered_date <= today + dt.timedelta(days=1500)
        ):
            raise PersonalDataError("Data terminu wygląda na błędną.")
        if report.outcome == "other" and report.offered_date is None:
            raise PersonalDataError("Podaj zaproponowany termin.")
        offered = report.offered_date
        if report.outcome == "same":
            offered = report.nfz_date
        if not report.reached or report.outcome == "none":
            offered = None
        day = today.isoformat()
        count = self.conn.execute(
            "SELECT count(*) FROM phone_reports WHERE user_id = ? AND report_day = ?",
            (user_id, day),
        ).fetchone()[0]
        if count >= MAX_REPORTS_PER_DAY:
            raise PersonalDataError("Dziś dodano już bardzo dużo zgłoszeń. Spróbuj jutro.")
        with self.conn:
            self.conn.execute(
                "INSERT INTO phone_reports (user_id, queue_id, report_day, created_at, reached,"
                " outcome, nfz_date, offered_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (user_id, queue_id, report_day) DO UPDATE SET"
                " created_at = excluded.created_at, reached = excluded.reached,"
                " outcome = excluded.outcome, nfz_date = excluded.nfz_date,"
                " offered_date = excluded.offered_date",
                (
                    user_id,
                    report.queue_id,
                    day,
                    self._now().isoformat(),
                    int(report.reached),
                    report.outcome if report.reached else None,
                    report.nfz_date.isoformat() if report.nfz_date else None,
                    offered.isoformat() if offered else None,
                ),
            )

    def summary(self, queue_ids: Iterable[str], today: dt.date) -> dict[str, dict[str, Any]]:
        """Zbiorcze zgłoszenia z ostatnich 14 dni dla podanych kolejek (bez danych osób)."""
        ids = [q for q in dict.fromkeys(queue_ids) if valid_queue_id(q)][:100]
        if not ids:
            return {}
        since = (today - dt.timedelta(days=REPORT_WINDOW_DAYS)).isoformat()
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            "SELECT queue_id, report_day, reached, outcome, nfz_date, offered_date"  # noqa: S608
            f" FROM phone_reports WHERE queue_id IN ({marks}) AND report_day >= ?",
            (*ids, since),
        ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["queue_id"], []).append(row)
        out: dict[str, dict[str, Any]] = {}
        for queue_id, items in grouped.items():
            deltas = [
                (
                    dt.date.fromisoformat(r["offered_date"]) - dt.date.fromisoformat(r["nfz_date"])
                ).days
                for r in items
                if r["offered_date"] and r["nfz_date"]
            ]
            out[queue_id] = {
                "reports": len(items),
                "not_reached": sum(1 for r in items if not r["reached"]),
                "same": sum(1 for r in items if r["outcome"] == "same"),
                "other": sum(1 for r in items if r["outcome"] == "other"),
                "none": sum(1 for r in items if r["outcome"] == "none"),
                "median_delta_days": round(statistics.median(deltas)) if deltas else None,
                "last_day": max(r["report_day"] for r in items),
                "days": REPORT_WINDOW_DAYS,
            }
        return out

    def for_user(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT queue_id, report_day, reached, outcome, nfz_date, offered_date"
            " FROM phone_reports WHERE user_id = ? ORDER BY report_day DESC",
            (user_id,),
        ).fetchall()
        return [{**dict(r), "reached": bool(r["reached"])} for r in rows]


# ---------------------------------------------------------------------- sprawy


def _clean(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value[:limit] or None


class CaseStore:
    FIELDS = ("label", "queue_id", "provider", "address", "phone", "status", "visit_date", "note")

    def __init__(self, conn: sqlite3.Connection, *, now: Callable[[], dt.datetime] = _now) -> None:
        self.conn = conn
        self._now = now

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {k: row[k] for k in ("id", *CaseStore.FIELDS, "created_at", "updated_at")}

    def _validate(self, data: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, limit in (("label", 200), ("provider", 300), ("address", 300), ("phone", 60)):
            if key in data:
                out[key] = _clean(data[key], limit)
        if "note" in data:
            out["note"] = _clean(data["note"], NOTE_MAX)
        if "queue_id" in data:
            qid = data["queue_id"]
            if qid is not None and not valid_queue_id(qid):
                raise PersonalDataError("Nieznana placówka.")
            out["queue_id"] = qid
        if "status" in data:
            if data["status"] not in CASE_STATUSES:
                raise PersonalDataError("Nieznany status sprawy.")
            out["status"] = data["status"]
        if "visit_date" in data:
            visit = data["visit_date"]
            out["visit_date"] = visit.isoformat() if isinstance(visit, dt.date) else None
        return out

    def create(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        values = self._validate(data)
        if not values.get("label"):
            raise PersonalDataError("Podaj, czego dotyczy sprawa (np. „okulista”).")
        count = self.conn.execute(
            "SELECT count(*) FROM cases WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        if count >= MAX_CASES:
            raise PersonalDataError("Masz już bardzo dużo spraw. Usuń zakończone.")
        now = self._now().isoformat()
        values.setdefault("status", "to_call")
        cols = ", ".join(values)
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO cases (user_id, {cols}, created_at, updated_at)"  # noqa: S608
                f" VALUES (?, {', '.join('?' * len(values))}, ?, ?)",
                (user_id, *values.values(), now, now),
            )
        created = self.get(user_id, int(cur.lastrowid or 0))
        assert created is not None
        return created

    def get(self, user_id: int, case_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM cases WHERE id = ? AND user_id = ?", (case_id, user_id)
        ).fetchone()
        return self._row(row) if row else None

    def list_for(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM cases WHERE user_id = ? ORDER BY"
            " CASE status WHEN 'done' THEN 2 WHEN 'rejected' THEN 2 ELSE 1 END,"
            " COALESCE(visit_date, '9999'), updated_at DESC",
            (user_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def update(self, user_id: int, case_id: int, data: dict[str, Any]) -> dict[str, Any]:
        values = self._validate(data)
        if "label" in values and not values["label"]:
            raise PersonalDataError("Nazwa sprawy nie może być pusta.")
        if self.get(user_id, case_id) is None:
            raise PersonalDataError("Nie ma takiej sprawy.")
        if values:
            sets = ", ".join(f"{k} = ?" for k in values)
            with self.conn:
                self.conn.execute(
                    f"UPDATE cases SET {sets}, updated_at = ? WHERE id = ? AND user_id = ?",  # noqa: S608
                    (*values.values(), self._now().isoformat(), case_id, user_id),
                )
        updated = self.get(user_id, case_id)
        assert updated is not None
        return updated

    def delete(self, user_id: int, case_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM cases WHERE id = ? AND user_id = ?", (case_id, user_id)
            )
        return cur.rowcount > 0

    def due_reminders(self, day: dt.date) -> list[tuple[int, dict[str, Any]]]:
        """Sprawy z wizytą w dniu `day`, o których jeszcze nie przypomnieliśmy."""
        rows = self.conn.execute(
            "SELECT * FROM cases WHERE visit_date = ? AND status NOT IN ('done', 'rejected')"
            " AND (reminded_for IS NULL OR reminded_for != visit_date)",
            (day.isoformat(),),
        ).fetchall()
        return [(int(r["user_id"]), self._row(r)) for r in rows]

    def mark_reminded(self, case_id: int, day: dt.date) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE cases SET reminded_for = ? WHERE id = ?", (day.isoformat(), case_id)
            )


# ---------------------------------------------------------------------- notatki


class NoteStore:
    def __init__(self, conn: sqlite3.Connection, *, now: Callable[[], dt.datetime] = _now) -> None:
        self.conn = conn
        self._now = now

    def get_many(self, user_id: int, queue_ids: Iterable[str]) -> dict[str, str]:
        ids = [q for q in dict.fromkeys(queue_ids) if valid_queue_id(q)][:100]
        if not ids:
            return {}
        rows = self.conn.execute(
            "SELECT queue_id, note FROM facility_notes WHERE user_id = ?"  # noqa: S608
            f" AND queue_id IN ({','.join('?' * len(ids))})",
            (user_id, *ids),
        ).fetchall()
        return {r["queue_id"]: r["note"] for r in rows}

    def put(self, user_id: int, queue_id: str, note: str) -> str | None:
        """Zapisuje notatkę; pusta usuwa. Zwraca zapisany tekst albo None."""
        if not valid_queue_id(queue_id):
            raise PersonalDataError("Nieznana placówka.")
        text = _clean(note, NOTE_MAX)
        with self.conn:
            if text is None:
                self.conn.execute(
                    "DELETE FROM facility_notes WHERE user_id = ? AND queue_id = ?",
                    (user_id, queue_id),
                )
                return None
            self.conn.execute(
                "INSERT INTO facility_notes (user_id, queue_id, note, updated_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (user_id, queue_id) DO UPDATE SET"
                " note = excluded.note, updated_at = excluded.updated_at",
                (user_id, queue_id, text, self._now().isoformat()),
            )
        return text

    def for_user(self, user_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT queue_id, note, updated_at FROM facility_notes WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def export_personal(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    """Sprawy, notatki, zgłoszenia i urządzenia push do eksportu danych (art. 15 RODO)."""
    devices = conn.execute(
        "SELECT device, created_at, last_ok_at FROM push_subscriptions WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    consent = conn.execute(
        "SELECT health_consent_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return {
        "health_consent_at": consent["health_consent_at"] if consent else None,
        "cases": CaseStore(conn).list_for(user_id),
        "facility_notes": NoteStore(conn).for_user(user_id),
        "phone_reports": PhoneReportStore(conn).for_user(user_id),
        "push_devices": [dict(r) for r in devices],
    }


MAX_IMPORT_ITEMS = 500


def _parse_date(value: Any) -> dt.date | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def import_personal(conn: sqlite3.Connection, user_id: int, data: dict[str, Any]) -> dict[str, int]:
    """Przywraca sprawy i notatki z pliku eksportu (`/api/account/export`).

    Przyjmuje cały eksport albo jego część `personal`. Pomija to, co już jest (ta sama sprawa:
    nazwa, placówka i data wizyty; notatka do tej samej poradni), więc import można powtórzyć.
    Zgłoszeń z telefonu nie przywracamy: liczą się tylko z ostatnich dni.
    """
    nested = data.get("personal")
    personal: dict[str, Any] = nested if isinstance(nested, dict) else data
    raw_cases, raw_notes = personal.get("cases"), personal.get("facility_notes")
    cases_in: list[Any] = raw_cases if isinstance(raw_cases, list) else []
    notes_in: list[Any] = raw_notes if isinstance(raw_notes, list) else []
    store = CaseStore(conn)
    existing = {(c["label"], c["provider"], c["visit_date"]) for c in store.list_for(user_id)}
    added_cases = skipped = 0
    for item in cases_in[:MAX_IMPORT_ITEMS]:
        if not isinstance(item, dict) or not isinstance(item.get("label"), str):
            skipped += 1
            continue
        visit = _parse_date(item.get("visit_date"))
        label = _clean(item["label"], 200)
        provider = _clean(item["provider"], 300) if isinstance(item.get("provider"), str) else None
        key = (label, provider, visit.isoformat() if visit else None)
        if key in existing:
            skipped += 1
            continue
        fields: dict[str, Any] = {
            k: item[k] if isinstance(item.get(k), str) else None
            for k in ("label", "provider", "address", "phone", "note")
        }
        fields["visit_date"] = visit
        if item.get("status") in CASE_STATUSES:
            fields["status"] = item["status"]
        if isinstance(item.get("queue_id"), str) and valid_queue_id(item["queue_id"]):
            fields["queue_id"] = item["queue_id"]
        try:
            store.create(user_id, fields)
        except PersonalDataError:
            skipped += 1
            continue
        existing.add(key)
        added_cases += 1
    notes = NoteStore(conn)
    added_notes = 0
    for item in notes_in[:MAX_IMPORT_ITEMS]:
        if not isinstance(item, dict):
            continue
        qid, note = item.get("queue_id"), item.get("note")
        if not (isinstance(qid, str) and isinstance(note, str) and valid_queue_id(qid)):
            continue
        if notes.get_many(user_id, [qid]):
            continue
        try:
            notes.put(user_id, qid, note)
        except PersonalDataError:
            continue
        added_notes += 1
    return {"cases": added_cases, "notes": added_notes, "skipped": skipped}
