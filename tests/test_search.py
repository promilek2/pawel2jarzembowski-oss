from __future__ import annotations

import pytest

from termin.core.benefits import Variant
from termin.core.models import GeoPoint, SortMode
from termin.core.ranking import is_empty_queue
from termin.core.search import SearchError, SearchRequest
from termin.runtime import Runtime
from tests.fake_nfz import FakeNfz

GDYNIA = "81-310 Gdynia"


async def test_acceptance_okulsta_gdynia_30km(runtime: Runtime) -> None:
    req = SearchRequest(text="okulsta", where=GDYNIA, radius_km=30, variant=Variant.ADULT)
    resp = await runtime.search.search(req)
    assert resp.resolution.entry is not None
    assert resp.resolution.entry.id == "okulista"
    assert resp.provinces[0] == "11"
    assert resp.results, "powinny być wyniki"
    for r in resp.results:
        assert r.entry.benefit == "ŚWIADCZENIA Z ZAKRESU OKULISTYKI"  # bez wariantu dla dzieci
        assert r.distance_km is not None and r.distance_km <= 30
    # pozycje z poprawnym terminem przed przeszłymi, pustymi kolejkami i bez terminu
    tiers = [
        0 if r.days_until is not None and r.days_until >= 0 and not is_empty_queue(r.entry) else 1
        for r in resp.results
    ]
    assert tiers == sorted(tiers)
    best = resp.results[0]
    assert best.explanation
    # API 1.4: prognoza PCUŚ zamiast terminu, położenie z TERYT, gdy brak współrzędnych
    forecasts = [r for r in resp.results if r.entry.is_forecast]
    assert forecasts and all(any(a.code == "forecast" for a in r.alerts) for r in forecasts)
    approx = [r for r in resp.results if r.entry.location_approx]
    assert approx and all(any(a.code == "approx_location" for a in r.alerts) for r in approx)
    assert resp.without_location == 0  # nikt nie wypada z wyników przez brak współrzędnych


async def test_ambiguous_variant_returns_question(runtime: Runtime) -> None:
    resp = await runtime.search.search(SearchRequest(text="okulista", where=GDYNIA))
    assert resp.needs_input
    assert resp.resolution.status == "choose_variant"
    assert resp.results == []


async def test_children_variant(runtime: Runtime) -> None:
    resp = await runtime.search.search(
        SearchRequest(text="okulista dla dzieci", where=GDYNIA, radius_km=100)
    )
    assert resp.results
    assert all(r.entry.is_for_children for r in resp.results)


async def test_fastest_mode_and_limit(runtime: Runtime) -> None:
    resp = await runtime.search.search(
        SearchRequest(
            text="okulista dorośli", where=GDYNIA, radius_km=200, mode=SortMode.FASTEST, limit=3
        )
    )
    assert len(resp.results) == 3
    assert resp.total_found > 3
    days = [r.days_until for r in resp.results]
    assert days == sorted(days)  # type: ignore[type-var]


async def test_many_places_uses_nearest_branch(runtime: Runtime) -> None:
    resp = await runtime.search.search(
        SearchRequest(text="okulista dorośli", where=GDYNIA, radius_km=30, limit=50)
    )
    slupsk = [r for r in resp.results if r.entry.many_places]
    assert slupsk, "placówka ze Słupska ma filię w Gdyni, więc powinna się zmieścić w 30 km"
    r = slupsk[0]
    assert r.location.locality == "GDYNIA"
    assert any(x.code == "nearest_branch" for x in r.reasons)


async def test_explicit_province_without_point(runtime: Runtime) -> None:
    resp = await runtime.search.search(
        SearchRequest(text="okulista dorośli", province="11", mode=SortMode.FASTEST, limit=100)
    )
    assert resp.point is None
    assert all(r.distance_km is None for r in resp.results)
    assert resp.total_found == 32  # 37 − 5 pozycji dla dzieci
    assert resp.medians["11"] > 0


async def test_errors(runtime: Runtime) -> None:
    with pytest.raises(SearchError, match="Podaj"):
        await runtime.search.search(SearchRequest(text="okulista dorośli"))
    with pytest.raises(SearchError, match="Nie udało się znaleźć"):
        await runtime.search.search(SearchRequest(text="okulista dorośli", where="Pcim Dolny"))
    with pytest.raises(SearchError, match="województwa"):
        await runtime.search.search(SearchRequest(text="okulista dorośli", province="42"))


async def test_benefit_query_override(runtime: Runtime) -> None:
    resp = await runtime.search.search(
        SearchRequest(
            text="",
            benefit_query="ŚWIADCZENIA Z ZAKRESU OKULISTYKI",
            variant=Variant.CHILD,
            point=GeoPoint(lat=54.52, lon=18.53, province="11"),
            radius_km=100,
        )
    )
    assert resp.results
    assert all(r.entry.is_for_children for r in resp.results)
    raw = await runtime.search.search(
        SearchRequest(text="", benefit_query="okulistyki", province="11", limit=100)
    )
    assert raw.resolution.entry is None
    assert raw.total_found == 37


async def test_radius_queries_neighbouring_provinces(runtime: Runtime, fake_nfz: FakeNfz) -> None:
    resp = await runtime.search.search(SearchRequest(text="rezonans", where="Elbląg", radius_km=80))
    assert {"11", "14"} <= set(resp.provinces)
    queried = {dict(r.url.params).get("province") for r in fake_nfz.requests_to("/queues")}
    assert {"11", "14"} <= queried
