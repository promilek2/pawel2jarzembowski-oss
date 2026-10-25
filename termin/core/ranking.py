"""Ranking wyników, ostrzeżenia o jakości danych i wyjaśnienia „dlaczego tu”.

Tryby:
- najszybciej: najwcześniejszy termin, przy remisie bliżej,
- najbliżej: najmniejsza odległość (albo czas dojazdu), przy remisie szybciej,
- kompromis: wynik = dni do terminu + km / km_per_day (albo min / minutes_per_day).
  `km_per_day` mówi, ile kilometrów dalej jestem gotów pojechać za każdy dzień
  wcześniejszego terminu (domyślnie 2 km/dzień, czyli 30 km za dwa tygodnie).

Pozycje z terminem w przeszłości (nieaktualne dane) i bez terminu zawsze lądują
za pozycjami z poprawnym terminem, żeby nie wprowadzały w błąd.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from termin.core.geo_math import haversine_km
from termin.core.i18n import t
from termin.core.models import (
    Alert,
    GeoPoint,
    Location,
    QueueEntry,
    RankedResult,
    Reason,
    SortMode,
)


@dataclass(frozen=True, slots=True)
class RankingOptions:
    today: dt.date
    mode: SortMode = SortMode.BALANCED
    km_per_day: float = 2.0
    minutes_per_day: float = 3.0
    stale_after_days: int = 14
    lang: str = "pl"


@dataclass(frozen=True, slots=True)
class Candidate:
    """Pozycja przed rankingiem: wpis NFZ + wybrana lokalizacja + odległość."""

    entry: QueueEntry
    location: Location
    province: str | None = None
    distance_km: float | None = None
    travel_min: float | None = None
    nearest_of_many: bool = False


def nearest_location(entry: QueueEntry, point: GeoPoint | None) -> tuple[Location, float | None]:
    """Najbliższa użytkownikowi lokalizacja placówki (dla many-places) i odległość."""
    locations = entry.all_locations
    if point is None:
        return locations[0], None
    best: tuple[Location, float | None] = (locations[0], None)
    for loc in locations:
        if loc.latitude is None or loc.longitude is None:
            continue
        d = haversine_km(point.lat, point.lon, loc.latitude, loc.longitude)
        if best[1] is None or d < best[1]:
            best = (loc, d)
    return best


FORECAST_STALE_DAYS = 45
"""PCUŚ NFZ liczy raz w miesiącu, więc „stan na” sprzed kilku tygodni to norma."""


def days_until(entry: QueueEntry, today: dt.date) -> int | None:
    expected = entry.expected_date(today)
    return (expected - today).days if expected else None


def alerts_for(
    entry: QueueEntry, location: Location, today: dt.date, stale_after_days: int
) -> tuple[Alert, ...]:
    out: list[Alert] = []
    first = entry.first_date
    if first is None and not entry.is_forecast:
        out.append(Alert(code="cer" if entry.in_cer else "no_date"))
    elif entry.is_forecast:
        out.append(Alert(code="forecast", params={"pcus": entry.pcus or ""}))
        if is_empty_queue(entry):
            out.append(Alert(code="empty_queue"))
    elif first is not None and first < today:
        out.append(Alert(code="past_date", params={"date": first.isoformat()}))
    as_at = entry.as_at
    if as_at is not None:
        age = (today - as_at).days
        limit = (
            max(stale_after_days, FORECAST_STALE_DAYS) if entry.is_forecast else stale_after_days
        )
        if age > limit:
            out.append(Alert(code="stale", params={"days": age, "as_at": as_at.isoformat()}))
    elif entry.dates is not None:
        out.append(Alert(code="no_as_at"))
    if not (location.phone or entry.phone):
        out.append(Alert(code="no_phone"))
    if entry.location_approx and location == entry.all_locations[0]:
        out.append(Alert(code="approx_location"))
    elif not location.has_coords:
        out.append(Alert(code="no_location"))
    return tuple(out)


def is_empty_queue(entry: QueueEntry) -> bool:
    """PCUŚ „0 dni” przy zerze oczekujących: zwykle nieczynna kolejka, nie wolne miejsce."""
    return bool(
        entry.is_forecast and entry.dates and entry.dates.pcus_days == 0 and entry.awaiting == 0
    )


def _tier(days: int | None, entry: QueueEntry | None = None) -> int:
    if days is None:
        return 2
    if days < 0 or (entry is not None and is_empty_queue(entry)):
        return 1
    return 0


def province_medians(
    items: Iterable[tuple[str | None, QueueEntry]], today: dt.date
) -> dict[str, float]:
    """Mediana dni do pierwszego terminu w województwie (tylko poprawne, przyszłe terminy)."""
    buckets: dict[str, list[int]] = defaultdict(list)
    for province, entry in items:
        d = days_until(entry, today)
        if province and d is not None and d >= 0 and not is_empty_queue(entry):
            buckets[province].append(d)
    return {p: float(statistics.median(v)) for p, v in buckets.items() if v}


def _score(c: Candidate, days: int | None, opts: RankingOptions, fallback_km: float) -> float:
    if days is None or days < 0:
        return math.inf
    if c.travel_min is not None:
        return days + c.travel_min / opts.minutes_per_day
    km = c.distance_km if c.distance_km is not None else fallback_km
    return days + km / opts.km_per_day


def rank(
    candidates: list[Candidate],
    opts: RankingOptions,
    medians: dict[str, float] | None = None,
) -> list[RankedResult]:
    medians = medians or {}
    known = [c.distance_km for c in candidates if c.distance_km is not None]
    fallback_km = max(known) if known else 0.0
    inf = math.inf

    prepared = []
    for c in candidates:
        days = days_until(c.entry, opts.today)
        score = _score(c, days, opts, fallback_km)
        dist = c.travel_min if c.travel_min is not None else c.distance_km
        dist_key = dist if dist is not None else inf
        days_key = days if days is not None and days >= 0 else inf
        if opts.mode is SortMode.FASTEST:
            key: tuple[float, ...] = (_tier(days, c.entry), days_key, dist_key)
        elif opts.mode is SortMode.NEAREST:
            key = (dist_key, _tier(days, c.entry), days_key)
        else:
            key = (_tier(days, c.entry), score, days_key, dist_key)
        prepared.append((key, c, days, score))

    prepared.sort(key=lambda item: (item[0], item[1].entry.id))
    results: list[RankedResult] = []
    for position, (_key, c, days, score) in enumerate(prepared):
        reasons = _reasons(c, days, position, opts, medians)
        results.append(
            RankedResult(
                entry=c.entry,
                location=c.location,
                distance_km=round(c.distance_km, 1) if c.distance_km is not None else None,
                travel_min=c.travel_min,
                days_until=days,
                expected_date=c.entry.expected_date(opts.today),
                score=round(score, 2) if math.isfinite(score) else None,
                province=c.province,
                reasons=reasons,
                alerts=alerts_for(c.entry, c.location, opts.today, opts.stale_after_days),
                explanation=explain(reasons, opts.lang),
            )
        )
    return results


def _reasons(
    c: Candidate,
    days: int | None,
    position: int,
    opts: RankingOptions,
    medians: dict[str, float],
) -> tuple[Reason, ...]:
    out: list[Reason] = []
    if position == 0 and days is not None and days >= 0:
        code = {
            SortMode.FASTEST: "earliest",
            SortMode.NEAREST: "nearest",
            SortMode.BALANCED: "best_balance",
        }[opts.mode]
        out.append(Reason(code=code))
    median = medians.get(c.province or "")
    if median is not None and days is not None and days >= 0:
        diff = round(median - days)
        if diff >= 1:
            out.append(Reason(code="faster_than_median", params={"days": diff}))
        elif diff <= -1:
            out.append(Reason(code="slower_than_median", params={"days": -diff}))
        else:
            out.append(Reason(code="typical_wait"))
    if c.travel_min is not None:
        out.append(Reason(code="travel_time", params={"min": round(c.travel_min)}))
    if c.distance_km is not None:
        km: int | float = round(c.distance_km) if c.distance_km >= 10 else round(c.distance_km, 1)
        out.append(Reason(code="distance", params={"km": km}))
    if c.nearest_of_many:
        out.append(Reason(code="nearest_branch"))
    return tuple(out)


def explain(reasons: Iterable[Reason], lang: str = "pl") -> str:
    """Jedno zdanie: „7 dni szybciej niż przeciętnie w województwie, 12 km od Ciebie”."""
    parts = [t(f"reasons.{r.code}", lang, **r.params) for r in reasons]
    if not parts:
        return ""
    sentence = ", ".join(parts)
    return sentence[0].upper() + sentence[1:]


def alert_text(alert: Alert, lang: str = "pl") -> str:
    params = dict(alert.params)
    for key in ("date", "as_at"):
        value = params.get(key)
        if isinstance(value, str):
            with contextlib.suppress(ValueError):
                params[key] = dt.date.fromisoformat(value).strftime("%d.%m.%Y")
    return t(f"alerts.{alert.code}", lang, **params)


def shorter_nearby(
    results: Iterable[RankedResult], min_gain_days: int = 7
) -> tuple[RankedResult, int] | None:
    """Placówka z najkrótszą prognozą, jeśli czeka się w niej wyraźnie krócej niż w najbliższej.

    Zwraca (placówka, o ile dni krócej) albo None. Pomija puste kolejki i wyniki bez odległości.
    """
    ok = [
        r
        for r in results
        if r.days_until is not None
        and r.days_until >= 0
        and r.distance_km is not None
        and not is_empty_queue(r.entry)
    ]
    if not ok:
        return None
    nearest = min(ok, key=lambda r: r.distance_km or 0.0)
    fastest = min(ok, key=lambda r: (r.days_until or 0, r.distance_km or 0.0))
    gain = (nearest.days_until or 0) - (fastest.days_until or 0)
    return (fastest, gain) if gain >= min_gain_days else None


def shorter_nearby_text(results: Iterable[RankedResult]) -> str | None:
    found = shorter_nearby(results)
    if found is None:
        return None
    best, gain = found
    place = (best.location.locality or best.entry.provider or "").title()
    return t("tips.shorter_nearby", place=place, km=best.distance_km, days=gain)
