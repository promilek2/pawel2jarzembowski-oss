from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from termin.config import Settings
from termin.core.geo import (
    Gazetteer,
    Geocoder,
    GeocodingError,
    normalize_postal,
    parse_coordinates,
)
from termin.core.routing import Router
from termin.store.db import connect

NOMINATIM_GDYNIA = [
    {
        "lat": "54.4985",
        "lon": "18.5280",
        "display_name": "Gdynia, województwo pomorskie, 81-310, Polska",
        "address": {"city": "Gdynia", "state": "województwo pomorskie", "postcode": "81-310"},
    }
]


@pytest.fixture
def gazetteer() -> Gazetteer:
    return Gazetteer.load()


def test_parse_coordinates() -> None:
    assert parse_coordinates("54.52, 18.53") == (54.52, 18.53)
    assert parse_coordinates("54,52 18,53") == (54.52, 18.53)
    assert parse_coordinates("48.85, 2.35") is None  # Paryż
    assert parse_coordinates("Gdynia") is None


def test_normalize_postal() -> None:
    assert normalize_postal("81-310 Gdynia") == "81-310"
    assert normalize_postal("81310") == "81-310"
    assert normalize_postal("Gdynia") is None


@pytest.mark.parametrize(
    ("text", "name", "province"),
    [
        ("81-310 Gdynia", "Gdynia", "11"),
        ("ul. Świętojańska 5, 81-310 Gdynia", "Gdynia", "11"),
        ("Ruda Śląska", "Ruda Śląska", "12"),
        ("bielsko-biala", "Bielsko-Biała", "12"),
        ("Nowy Sącz, ul. Długa 3", "Nowy Sącz", "06"),
        ("LODZ", "Łódź", "05"),
    ],
)
def test_gazetteer_lookup(gazetteer: Gazetteer, text: str, name: str, province: str) -> None:
    point = gazetteer.lookup(text)
    assert point is not None
    assert point.label == name
    assert point.province == province
    assert point.source == "offline"


def test_gazetteer_unknown(gazetteer: Gazetteer) -> None:
    assert gazetteer.lookup("Pcim Dolny 7") is None


def test_gazetteer_postal_table(tmp_path: Path) -> None:
    table = tmp_path / "postal_codes.csv"
    table.write_text("postal_code,name,province,lat,lon\n81-310,Gdynia,11,54.50,18.53\n")
    gz = Gazetteer.load(table)
    point = gz.lookup("81-310")
    assert point is not None
    assert point.source == "offline-postal"
    assert point.label == "81-310 Gdynia"


def test_province_for_point(gazetteer: Gazetteer) -> None:
    assert gazetteer.province_for_point(54.40, 18.60) == "11"
    assert gazetteer.province_for_point(50.05, 19.94) == "06"


class Clock:
    def __init__(self) -> None:
        self.t = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def _nominatim(handler_calls: list[httpx.Request], payload: object, status: int = 200):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        handler_calls.append(request)
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


async def test_nominatim_with_cache_and_rate_limit(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, geocoder="nominatim")
    calls: list[httpx.Request] = []
    clock = Clock()
    Geocoder._next_slot = 0.0
    conn = connect(tmp_path / "g.db")
    geo = Geocoder(
        settings,
        conn=conn,
        transport=_nominatim(calls, NOMINATIM_GDYNIA),
        sleep=clock.sleep,
        clock=clock,
    )
    p1 = await geo.geocode("81-310 Gdynia")
    p2 = await geo.geocode("81-310  gdynia")  # z cache (po normalizacji)
    p3 = await geo.geocode("Sopot, Monte Cassino")  # drugie żądanie: musi poczekać 1 s
    await geo.aclose()
    conn.close()
    assert p1 is not None and p1.source == "nominatim"
    assert p1.province == "11"
    assert p1.label == "81-310 Gdynia"
    assert p2 is not None and p2.source == "cache"
    assert p3 is not None
    assert len(calls) == 2
    params = dict(calls[0].url.params)
    assert params["countrycodes"] == "pl"
    assert params["format"] == "jsonv2"
    assert "termin/" in calls[0].headers["User-Agent"]
    assert clock.sleeps == [1.0]


async def test_auto_falls_back_to_offline_when_nominatim_down(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, geocoder="auto")
    calls: list[httpx.Request] = []
    Geocoder._next_slot = 0.0
    geo = Geocoder(settings, transport=_nominatim(calls, {}, status=503))
    point = await geo.geocode("81-310 Gdynia")
    await geo.aclose()
    assert point is not None
    assert point.source == "offline"
    assert point.province == "11"


async def test_nominatim_mode_raises_when_down(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, geocoder="nominatim")
    Geocoder._next_slot = 0.0

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x", request=request)

    geo = Geocoder(settings, transport=httpx.MockTransport(boom))
    with pytest.raises(GeocodingError):
        await geo.geocode("Gdynia")
    await geo.aclose()


async def test_nominatim_empty_and_foreign_results(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, geocoder="nominatim")
    Geocoder._next_slot = 0.0
    calls: list[httpx.Request] = []
    clock = Clock()
    geo = Geocoder(settings, transport=_nominatim(calls, []), sleep=clock.sleep, clock=clock)
    assert await geo.geocode("nigdzie") is None
    await geo.aclose()
    paris = [{"lat": "48.85", "lon": "2.35", "address": {}}]
    geo = Geocoder(settings, transport=_nominatim(calls, paris), sleep=clock.sleep, clock=clock)
    assert await geo.geocode("Paryż") is None
    await geo.aclose()


async def test_coordinates_and_offline_mode(tmp_path: Path) -> None:
    geo = Geocoder(Settings(data_dir=tmp_path, geocoder="offline"))
    gps = await geo.geocode("54.40, 18.60")
    assert gps is not None
    assert gps.source == "gps"
    assert gps.province == "11"
    assert await geo.geocode("") is None
    offline = await geo.geocode("Kraków")
    assert offline is not None and offline.province == "06"
    await geo.aclose()


async def test_osrm_table() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"code": "Ok", "durations": [[0, 600, None, 1830]]})

    router = Router("https://osrm.example/", transport=httpx.MockTransport(handler))
    out = await router.durations_min((54.5, 18.5), [(54.4, 18.6), (54.3, 18.7), (54.2, 18.8)])
    await router.aclose()
    assert out == [10.0, None, 30.5]
    assert "/table/v1/driving/18.500000,54.500000;" in seen[0].url.path
    assert dict(seen[0].url.params)["sources"] == "0"


async def test_osrm_failure_is_graceful() -> None:
    router = Router(
        "https://osrm.example", transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    assert await router.durations_min((54.5, 18.5), [(54.4, 18.6)]) == [None]
    assert await router.durations_min((54.5, 18.5), []) == []
    await router.aclose()


def test_nominatim_fixture_is_json() -> None:
    json.dumps(NOMINATIM_GDYNIA)
