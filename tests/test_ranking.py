from __future__ import annotations

import datetime as dt

import pytest

from termin.core.i18n import t, when
from termin.core.models import GeoPoint, Location, QueueEntry, SortMode
from termin.core.ranking import (
    Candidate,
    RankingOptions,
    alert_text,
    alerts_for,
    explain,
    nearest_location,
    province_medians,
    rank,
)
from tests.conftest import TODAY


def entry(
    idx: str,
    date: str | None,
    *,
    as_at: str | None = "2026-10-20",
    phone: str | None = "58 1",
    lat: float | None = 54.5,
    lon: float | None = 18.5,
) -> QueueEntry:
    dates = (
        None if date is None and as_at is None else {"date": date, "date-situation-as-at": as_at}
    )
    return QueueEntry.model_validate(
        {
            "id": idx,
            "benefit": "PORADNIA OKULISTYCZNA",
            "dates": dates,
            "phone": phone,
            "latitude": lat,
            "longitude": lon,
        }
    )


def cand(e: QueueEntry, km: float | None, province: str = "11") -> Candidate:
    return Candidate(entry=e, location=e.all_locations[0], province=province, distance_km=km)


@pytest.fixture
def pool() -> list[Candidate]:
    return [
        cand(entry("near-slow", "2027-01-22"), 2.0),  # 90 dni, 2 km
        cand(entry("far-fast", "2026-10-31"), 60.0),  # 7 dni, 60 km
        cand(entry("mid", "2026-11-23"), 12.0),  # 30 dni, 12 km
        cand(entry("past", "2026-10-01"), 1.0),  # termin w przeszłości
        cand(entry("none", None), 0.5),  # brak terminu
    ]


def ids(results: list) -> list[str]:  # type: ignore[type-arg]
    return [r.entry.id for r in results]


def test_fastest(pool: list[Candidate]) -> None:
    out = rank(pool, RankingOptions(today=TODAY, mode=SortMode.FASTEST))
    assert ids(out) == ["far-fast", "mid", "near-slow", "past", "none"]
    assert out[0].days_until == 7


def test_nearest(pool: list[Candidate]) -> None:
    out = rank(pool, RankingOptions(today=TODAY, mode=SortMode.NEAREST))
    assert ids(out) == ["none", "past", "near-slow", "mid", "far-fast"]


def test_balanced_default_weight(pool: list[Candidate]) -> None:
    # 2 km/dzień: far-fast = 7 + 30 = 37, mid = 30 + 6 = 36, near-slow = 90 + 1 = 91
    out = rank(pool, RankingOptions(today=TODAY, mode=SortMode.BALANCED, km_per_day=2.0))
    assert ids(out)[:3] == ["mid", "far-fast", "near-slow"]
    assert ids(out)[3:] == ["past", "none"]
    assert out[0].score == 36.0


def test_balanced_weight_changes_order(pool: list[Candidate]) -> None:
    # 10 km/dzień: odległość mało waży → far-fast (7 + 6 = 13) wygrywa
    out = rank(pool, RankingOptions(today=TODAY, mode=SortMode.BALANCED, km_per_day=10.0))
    assert ids(out)[0] == "far-fast"
    # 0,2 km/dzień: odległość bardzo waży → near-slow (90 + 10 = 100) przed far-fast (307)
    out = rank(pool, RankingOptions(today=TODAY, mode=SortMode.BALANCED, km_per_day=0.2))
    assert ids(out).index("near-slow") < ids(out).index("far-fast")


def test_balanced_prefers_travel_time_when_known() -> None:
    a = Candidate(entry("a", "2026-11-03"), entry("a", None).all_locations[0], "11", 10, 60.0)
    b = Candidate(entry("b", "2026-11-04"), entry("b", None).all_locations[0], "11", 30, 20.0)
    out = rank([a, b], RankingOptions(today=TODAY, minutes_per_day=3.0))
    # a: 10 + 60/3 = 30, b: 11 + 20/3 ≈ 17.7
    assert ids(out) == ["b", "a"]


def test_unknown_distance_does_not_win_balanced() -> None:
    known = cand(entry("known", "2026-11-03"), 10.0)
    unknown = cand(entry("unknown", "2026-11-03", lat=None, lon=None), None)
    out = rank([unknown, known], RankingOptions(today=TODAY))
    assert ids(out) == ["known", "unknown"]


def test_alerts() -> None:
    e = entry("x", "2026-10-01", as_at="2026-09-01", phone=None, lat=None, lon=None)
    codes = [a.code for a in alerts_for(e, e.all_locations[0], TODAY, 14)]
    assert codes == ["past_date", "stale", "no_phone", "no_location"]
    fresh = entry("y", "2026-11-01")
    assert alerts_for(fresh, fresh.all_locations[0], TODAY, 14) == ()
    no_date = entry("z", None, as_at=None)
    assert [a.code for a in alerts_for(no_date, no_date.all_locations[0], TODAY, 14)] == ["no_date"]
    no_as_at = entry("w", "2026-11-01", as_at=None)
    assert "no_as_at" in [
        a.code for a in alerts_for(no_as_at, no_as_at.all_locations[0], TODAY, 14)
    ]


def test_stale_boundary() -> None:
    e = entry("x", "2026-11-01", as_at=(TODAY - dt.timedelta(days=14)).isoformat())
    assert alerts_for(e, e.all_locations[0], TODAY, 14) == ()
    e = entry("x", "2026-11-01", as_at=(TODAY - dt.timedelta(days=15)).isoformat())
    assert [a.code for a in alerts_for(e, e.all_locations[0], TODAY, 14)] == ["stale"]


def test_explanations(pool: list[Candidate]) -> None:
    medians = province_medians([(c.province, c.entry) for c in pool], TODAY)
    assert medians == {"11": 30.0}  # 7, 30, 90 (przeszłe i brak pomijamy)
    out = rank(pool, RankingOptions(today=TODAY, mode=SortMode.FASTEST), medians)
    first = out[0]
    assert first.explanation == (
        "Najwcześniejszy termin w wynikach, 23 dni szybciej niż przeciętnie w województwie, "
        "60 km od Ciebie"
    )
    mid = next(r for r in out if r.entry.id == "mid")
    assert mid.explanation == "Termin typowy dla województwa, 12 km od Ciebie"
    slow = next(r for r in out if r.entry.id == "near-slow")
    assert "60 dni później niż przeciętnie" in slow.explanation
    assert slow.explanation.endswith(", 2 km od Ciebie")


def test_explain_single_day_plural() -> None:
    from termin.core.models import Reason

    assert explain([Reason(code="faster_than_median", params={"days": 1})]) == (
        "1 dzień szybciej niż przeciętnie w województwie"
    )
    assert explain([]) == ""


def test_nearest_location_many_places() -> None:
    main = entry("m", "2026-11-01", lat=54.46, lon=17.03)  # Słupsk
    branch = Location(place="FILIA", locality="GDYNIA", latitude=54.51, longitude=18.52)
    e = main.model_copy(update={"extra_locations": (branch,)})
    user = GeoPoint(lat=54.5189, lon=18.5305)
    loc, dist = nearest_location(e, user)
    assert loc.locality == "GDYNIA"
    assert dist is not None and dist < 2
    loc, dist = nearest_location(e, None)
    assert dist is None


def test_i18n_helpers() -> None:
    assert when(0) == "dziś"
    assert when(1) == "jutro"
    assert when(5) == "za 5 dni"
    assert when(-2) == "2 dni temu"
    assert t("missing.key") == "missing.key"
    assert t("alerts.no_phone", lang="xx") == "brak numeru telefonu w danych NFZ"
    from termin.core.models import Alert

    assert alert_text(Alert(code="stale", params={"days": 22, "as_at": "2026-10-02"})) == (
        "dane sprzed 22 dni (stan na 02.10.2026)"
    )


def test_format_number() -> None:
    from termin.core.i18n import format_number

    assert format_number(2.0) == "2"
    assert format_number(2.54) == "2,5"
    assert format_number(2.54, "en") == "2.5"


def test_shorter_nearby(pool: list[Candidate]) -> None:
    from termin.core.ranking import shorter_nearby, shorter_nearby_text

    results = rank(pool, RankingOptions(today=TODAY, mode=SortMode.NEAREST))
    found = shorter_nearby(results)
    assert found is not None
    best, gain = found
    assert best.entry.id == "far-fast" and gain == 83  # 90 dni (2 km) vs 7 dni (60 km)
    text = shorter_nearby_text(results)
    assert text is not None and "60 km" in text and "83 dni krócej" in text
    # bez wyraźnej różnicy (< 7 dni) nie ma podpowiedzi
    close = [cand(entry("a", "2026-11-23"), 2.0), cand(entry("b", "2026-11-20"), 30.0)]
    assert shorter_nearby(rank(close, RankingOptions(today=TODAY))) is None
    assert shorter_nearby([]) is None
