"""Snapshoty wyników NFZ: historia placówek, trendy, statystyki województw, eksport.

Zapisujemy wyłącznie dane publiczne NFZ. Lokalizacja i zapytanie użytkownika nie trafiają
do bazy (klucz snapshotu to fragment nazwy świadczenia, województwo i przypadek).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sqlite3
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from termin.core.models import QueueEntry
from termin.core.provinces import province_from_teryt

Direction = Literal["shorter", "longer", "stable", "unknown"]

TREND_THRESHOLD_DAYS_PER_WEEK = 1.0
"""Zmiana oczekiwania szybsza niż 1 dzień na tydzień to trend, wolniejsza to stabilnie."""

EXPORT_COLUMNS = (
    "taken_on",
    "query",
    "snapshot_province",
    "case_no",
    "queue_id",
    "benefit",
    "provider",
    "place",
    "address",
    "locality",
    "phone",
    "province",
    "teryt",
    "first_date",
    "as_at",
    "wait_days",
    "awaiting",
    "average_period",
    "latitude",
    "longitude",
    "for_children",
)


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    taken_on: dt.date
    first_date: dt.date | None
    as_at: dt.date | None
    awaiting: int | None

    @property
    def wait_days(self) -> int | None:
        return (self.first_date - self.taken_on).days if self.first_date else None


@dataclass(frozen=True, slots=True)
class Trend:
    points: list[HistoryPoint]
    direction: Direction
    slope_days_per_week: float | None
    change_days: int | None
    """Zmiana czasu oczekiwania między pierwszym a ostatnim punktem (ujemna = krócej)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "slope_days_per_week": self.slope_days_per_week,
            "change_days": self.change_days,
            "points": [
                {
                    "taken_on": p.taken_on.isoformat(),
                    "first_date": p.first_date.isoformat() if p.first_date else None,
                    "as_at": p.as_at.isoformat() if p.as_at else None,
                    "wait_days": p.wait_days,
                    "awaiting": p.awaiting,
                }
                for p in self.points
            ],
        }


@dataclass(frozen=True, slots=True)
class DistrictStat:
    teryt: str
    """Kod TERYT powiatu (4 cyfry)."""
    localities: list[str]
    n: int
    median_days: float
    lat: float | None
    lon: float | None


@dataclass(frozen=True, slots=True)
class WaitStats:
    query: str
    province: str
    case_no: int
    taken_on: dt.date | None
    n: int
    with_date: int
    median: float | None = None
    p25: float | None = None
    p75: float | None = None
    p90: float | None = None
    minimum: int | None = None
    maximum: int | None = None
    districts: list[DistrictStat] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["taken_on"] = self.taken_on.isoformat() if self.taken_on else None
        return data


def _iso(value: dt.date | None) -> str | None:
    return value.isoformat() if value else None


def _date(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value) if value else None


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Percentyl z interpolacją liniową (jak numpy 'linear'); `sorted_values` niepuste."""
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)


def compute_trend(points: list[HistoryPoint]) -> Trend:
    usable = [(p.taken_on, p.wait_days) for p in points if p.wait_days is not None]
    if len(usable) < 2:
        return Trend(points, "unknown", None, None)
    x0 = usable[0][0]
    xs = [(d - x0).days / 7 for d, _ in usable]
    ys = [float(w) for _, w in usable if w is not None]
    if xs[-1] == xs[0]:
        return Trend(points, "unknown", None, None)
    slope = statistics.linear_regression(xs, ys).slope
    change = round(ys[-1] - ys[0])
    if slope <= -TREND_THRESHOLD_DAYS_PER_WEEK:
        direction: Direction = "shorter"
    elif slope >= TREND_THRESHOLD_DAYS_PER_WEEK:
        direction = "longer"
    else:
        direction = "stable"
    return Trend(points, direction, round(slope, 2), change)


class SnapshotStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ------------------------------------------------------------------ zapis

    def save(
        self,
        *,
        query: str,
        province: str,
        case_no: int,
        entries: Iterable[QueueEntry],
        source: str = "search",
        now: dt.datetime | None = None,
    ) -> int:
        """Zapisuje snapshot (zastępuje dzisiejszy dla tego samego klucza). Zwraca id."""
        now = now or dt.datetime.now(dt.UTC)
        taken_on = now.date().isoformat()
        with self.conn:
            self.conn.execute(
                "DELETE FROM snapshots WHERE query = ? AND province = ? AND case_no = ?"
                " AND taken_on = ?",
                (query, province, case_no, taken_on),
            )
            cur = self.conn.execute(
                "INSERT INTO snapshots (taken_at, taken_on, query, province, case_no, source)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (now.isoformat(), taken_on, query, province, case_no, source),
            )
            snapshot_id = int(cur.lastrowid or 0)
            rows = {
                e.id: (
                    snapshot_id,
                    e.id,
                    e.benefit,
                    e.provider,
                    e.place,
                    e.address,
                    e.locality,
                    e.phone,
                    province_from_teryt(e.teryt_place) or province,
                    e.teryt_place,
                    _iso(e.first_date),
                    _iso(e.as_at),
                    e.awaiting,
                    e.average_period,
                    e.latitude,
                    e.longitude,
                    int(e.is_for_children),
                )
                for e in entries
            }
            self.conn.executemany(
                "INSERT INTO snapshot_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?)",
                rows.values(),
            )
        return snapshot_id

    def purge(self, older_than_days: int, today: dt.date | None = None) -> int:
        cutoff = (today or dt.datetime.now(dt.UTC).date()) - dt.timedelta(days=older_than_days)
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM snapshots WHERE taken_on < ?", (cutoff.isoformat(),)
            )
        return cur.rowcount

    # ------------------------------------------------------------------ odczyt

    def history(self, queue_id: str, days: int | None = None) -> list[HistoryPoint]:
        """Historia pierwszego terminu placówki: jeden punkt na dzień (najnowszy)."""
        sql = (
            "SELECT s.taken_on, r.first_date, r.as_at, r.awaiting, s.taken_at"
            " FROM snapshot_rows r JOIN snapshots s ON s.id = r.snapshot_id"
            " WHERE r.queue_id = ?"
        )
        params: list[Any] = [queue_id]
        if days is not None:
            sql += " AND s.taken_on >= date((SELECT max(taken_on) FROM snapshots), ?)"
            params.append(f"-{days} days")
        sql += " ORDER BY s.taken_on, s.taken_at"
        by_day: dict[str, HistoryPoint] = {}
        for row in self.conn.execute(sql, params):
            by_day[row[0]] = HistoryPoint(
                taken_on=dt.date.fromisoformat(row[0]),
                first_date=_date(row[1]),
                as_at=_date(row[2]),
                awaiting=row[3],
            )
        return list(by_day.values())

    def trend(self, queue_id: str, days: int | None = 90) -> Trend:
        return compute_trend(self.history(queue_id, days))

    def latest_snapshot_id(
        self, query: str, province: str, case_no: int, on: dt.date | None = None
    ) -> tuple[int, dt.date] | None:
        sql = "SELECT id, taken_on FROM snapshots WHERE query = ? AND province = ? AND case_no = ?"
        params: list[Any] = [query, province, case_no]
        if on is not None:
            sql += " AND taken_on <= ?"
            params.append(on.isoformat())
        row = self.conn.execute(sql + " ORDER BY taken_on DESC LIMIT 1", params).fetchone()
        return (int(row[0]), dt.date.fromisoformat(row[1])) if row else None

    def wait_stats(
        self,
        query: str,
        province: str,
        case_no: int = 1,
        *,
        on: dt.date | None = None,
        for_children: bool | None = None,
    ) -> WaitStats:
        """Mediana i percentyle czasu oczekiwania (dni od snapshotu do pierwszego terminu)."""
        latest = self.latest_snapshot_id(query, province, case_no, on)
        if latest is None:
            return WaitStats(query, province, case_no, None, 0, 0)
        snapshot_id, taken_on = latest
        sql = (
            "SELECT first_date, teryt, locality, latitude, longitude, for_children"
            " FROM snapshot_rows WHERE snapshot_id = ?"
        )
        rows = self.conn.execute(sql, (snapshot_id,)).fetchall()
        if for_children is not None:
            rows = [r for r in rows if bool(r[5]) == for_children]

        waits: list[int] = []
        by_district: dict[str, list[tuple[int, str | None, float | None, float | None]]] = (
            defaultdict(list)
        )
        for first, teryt, locality, lat, lon, _children in rows:
            first_date = _date(first)
            if first_date is None:
                continue
            wait = (first_date - taken_on).days
            if wait < 0:
                continue  # termin w przeszłości = nieaktualne dane
            waits.append(wait)
            if teryt and len(teryt) >= 4:
                by_district[teryt[:4]].append((wait, locality, lat, lon))

        base = WaitStats(query, province, case_no, taken_on, len(rows), len(waits))
        if not waits:
            return base
        waits.sort()
        districts = []
        for code, items in sorted(by_district.items()):
            coords = [(lat, lon) for _, _, lat, lon in items if lat is not None and lon is not None]
            districts.append(
                DistrictStat(
                    teryt=code,
                    localities=sorted({loc for _, loc, _, _ in items if loc}),
                    n=len(items),
                    median_days=float(statistics.median(w for w, *_ in items)),
                    lat=round(statistics.fmean(c[0] for c in coords), 4) if coords else None,
                    lon=round(statistics.fmean(c[1] for c in coords), 4) if coords else None,
                )
            )
        return WaitStats(
            query=query,
            province=province,
            case_no=case_no,
            taken_on=taken_on,
            n=len(rows),
            with_date=len(waits),
            median=float(statistics.median(waits)),
            p25=round(percentile(waits, 0.25), 1),
            p75=round(percentile(waits, 0.75), 1),
            p90=round(percentile(waits, 0.90), 1),
            minimum=waits[0],
            maximum=waits[-1],
            districts=districts,
        )

    def export_rows(
        self,
        *,
        query: str | None = None,
        province: str | None = None,
        since: dt.date | None = None,
        until: dt.date | None = None,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT s.taken_on, s.query, s.province AS snapshot_province, s.case_no, r.*"
            " FROM snapshot_rows r JOIN snapshots s ON s.id = r.snapshot_id WHERE 1 = 1"
        )
        params: list[Any] = []
        if query:
            sql += " AND s.query = ?"
            params.append(query)
        if province:
            sql += " AND s.province = ?"
            params.append(province)
        if since:
            sql += " AND s.taken_on >= ?"
            params.append(since.isoformat())
        if until:
            sql += " AND s.taken_on <= ?"
            params.append(until.isoformat())
        sql += " ORDER BY s.taken_on, s.query, s.province, r.queue_id"
        out = []
        for row in self.conn.execute(sql, params):
            # sqlite3.Row iteruje po wartościach, więc klucze bierzemy jawnie
            item = {key: row[key] for key in row.keys() if key != "snapshot_id"}  # noqa: SIM118
            first = _date(item["first_date"])
            item["wait_days"] = (
                (first - dt.date.fromisoformat(item["taken_on"])).days if first else None
            )
            item["for_children"] = bool(item["for_children"])
            out.append({col: item.get(col) for col in EXPORT_COLUMNS})
        return out


def to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(EXPORT_COLUMNS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def to_json(rows: list[dict[str, Any]]) -> str:
    meta = {
        "source": "NFZ „Terminy leczenia” (api.nfz.gov.pl), snapshoty zebrane przez Termin",
        "license_note": "Dane publiczne NFZ. Podaj źródło przy publikacji.",
        "count": len(rows),
    }
    return json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False, indent=2)
