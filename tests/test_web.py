from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from termin.config import Settings
from termin.runtime import Transports
from termin.web.app import create_app
from tests.conftest import TODAY
from tests.fake_nfz import FakeNfz
from tests.fake_services import FakeNotifyServices

STATIC = Path(__file__).resolve().parents[1] / "termin" / "web" / "static"


@pytest.fixture
def fake() -> FakeNfz:
    return FakeNfz.with_defaults()


@pytest.fixture
def client(tmp_path: Path, fake: FakeNfz) -> Iterator[TestClient]:
    settings = Settings(
        data_dir=tmp_path, geocoder="offline", public_url="https://termin.example",
        telegram_token="1:A",
    )  # fmt: skip
    app = create_app(
        settings,
        transports=Transports(nfz=fake.transport(), notify=FakeNotifyServices().transport()),
        today=lambda: TODAY,
    )
    with TestClient(app) as c:
        yield c


def test_index_and_static(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert '<html lang="pl">' in r.text
    assert "Content-Security-Policy" in r.headers
    assert "tile.openstreetmap.org" in r.headers["Content-Security-Policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/vendor/leaflet/leaflet.js").status_code == 200
    sw = client.get("/sw.js")
    assert sw.status_code == 200
    assert "javascript" in sw.headers["content-type"]
    manifest = client.get("/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    data = manifest.json()
    sizes = {i["sizes"] for i in data["icons"]}
    assert {"192x192", "512x512"} <= sizes
    assert any(i.get("purpose") == "maskable" for i in data["icons"])
    for icon in data["icons"]:
        assert client.get(icon["src"]).status_code == 200
    assert "Disallow: /api/" in client.get("/robots.txt").text


def test_config(client: TestClient) -> None:
    cfg = client.get("/api/config").json()
    assert cfg["channels"] == ["ntfy", "telegram"]
    assert cfg["provinces"]["11"] == "pomorskie"
    assert cfg["official_url"].startswith("https://terminyleczenia.nfz.gov.pl")


def test_suggest(client: TestClient) -> None:
    items = client.get("/api/suggest", params={"q": "okul"}).json()
    assert items[0]["id"] == "okulista"
    assert items[0]["has_child_variant"] is True
    assert client.get("/api/suggest", params={"q": ""}).json() == []


def test_search_flow(client: TestClient) -> None:
    first = client.get(
        "/api/search", params={"q": "okulsta", "where": "81-310 Gdynia", "radius": 30}
    )
    assert first.status_code == 200
    assert first.json()["status"] == "choose_variant"
    r = client.get(
        "/api/search",
        params={"q": "okulsta", "where": "81-310 Gdynia", "radius": 30, "variant": "dorosli"},
    )
    data = r.json()
    assert data["status"] == "ok"
    assert data["results"]
    top = data["results"][0]
    assert top["locality"] == "GDYNIA"
    assert top["reasons"]
    assert data["point"]["source"] == "offline"


def test_search_with_gps_and_modes(client: TestClient) -> None:
    r = client.get(
        "/api/search",
        params={"q": "rezonans", "lat": 54.52, "lon": 18.53, "mode": "najszybciej"},
    )
    assert r.status_code == 200
    assert r.json()["results"] == []
    amb = client.get("/api/search", params={"q": "kolano", "where": "Gdynia"}).json()
    assert amb["status"] == "ambiguous"
    assert amb["candidates"][0]["query"]


def test_search_errors(client: TestClient, fake: FakeNfz) -> None:
    assert client.get("/api/search", params={"q": ""}).status_code == 400
    r = client.get("/api/search", params={"q": "rezonans", "where": "Pcim Dolny 7"})
    assert r.status_code == 400
    assert r.json()["code"] == "search"
    assert client.get("/api/search", params={"q": "x", "lat": 10, "lon": 10}).status_code == 422
    fake.fail_with = [400]
    r = client.get("/api/search", params={"q": "zaćma", "where": "Gdynia"})
    assert r.status_code == 502
    assert r.json()["code"] == "nfz"


def test_history_stats_export(client: TestClient) -> None:
    client.get("/api/search", params={"q": "okulista dorośli", "where": "Gdynia"})
    hist = client.get("/api/history/00000001-0000-4000-8000-000000000001").json()
    assert hist["direction"] == "unknown"
    assert len(hist["points"]) == 1
    stats = client.get("/api/stats", params={"q": "okulista", "province": "11"}).json()
    assert stats["n"] == 30
    assert stats["median"] is not None
    assert stats["province_name"] == "pomorskie"
    assert stats["districts"]
    adults = client.get(
        "/api/stats", params={"q": "okulista", "province": "11", "variant": "dorosli"}
    ).json()
    assert adults["n"] == 26
    assert client.get("/api/stats", params={"q": "okulista", "province": "99"}).status_code == 400
    csv_resp = client.get("/api/export.csv", params={"q": "okulista", "province": "11"})
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert csv_resp.text.startswith("taken_on,")
    js = client.get("/api/export.json").json()
    assert js["meta"]["count"] == 30


def test_ics(client: TestClient) -> None:
    r = client.get("/api/ics/00000001-0000-4000-8000-000000000001", params={"day": "2026-10-26"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    body = r.text
    assert "BEGIN:VCALENDAR" in body
    assert "DTSTART:20261026T080000" in body
    assert "SUMMARY:Zadzwoń: PRZYCHODNIA PRZYKŁADOWA NR 1 SP. Z O.O." in body
    assert "\r\n" in body
    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))
    default_day = client.get("/api/ics/00000001-0000-4000-8000-000000000001")
    assert "DTSTART:20261025T080000" in default_day.text
    assert client.get("/api/ics/brak").status_code == 404


def test_subscriptions_api_and_stop_page(client: TestClient) -> None:
    payload = {
        "q": "okulista",
        "variant": "dorosli",
        "where": "Gdynia",
        "radius": 30,
        "channel": "ntfy",
        "target": "termin-abc",
    }
    r = client.post("/api/subscriptions", json=payload)
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["unsubscribe_url"] == f"https://termin.example/stop/{sub['token']}"
    assert "target" not in sub
    token = sub["token"]
    assert client.get(f"/api/subscriptions/{token}").json()["label"].startswith("Okulista")

    page = client.get(f"/stop/{token}")
    assert page.status_code == 200
    assert "Przestań obserwować" in page.text
    assert client.get(f"/api/subscriptions/{token}").status_code == 200  # GET niczego nie usuwa
    done = client.post(f"/stop/{token}")
    assert "Gotowe" in done.text
    assert client.get(f"/api/subscriptions/{token}").status_code == 404
    assert client.get(f"/stop/{token}").status_code == 404
    assert client.post(f"/stop/{token}").status_code == 404

    gps = client.post(
        "/api/subscriptions", json={**payload, "where": None, "lat": 54.5, "lon": 18.5}
    )
    assert gps.status_code == 201
    assert client.delete(f"/api/subscriptions/{gps.json()['token']}").status_code == 204
    assert client.delete(f"/api/subscriptions/{gps.json()['token']}").status_code == 404

    bad = client.post("/api/subscriptions", json={**payload, "q": "kolano"})
    assert bad.status_code == 400
    assert "Doprecyzuj" in bad.json()["detail"]
    email = client.post(
        "/api/subscriptions", json={**payload, "channel": "email", "target": "a@b.pl"}
    )
    assert email.status_code == 400
    assert client.post("/api/subscriptions", json={**payload, "channel": "sms"}).status_code == 422


def test_i18n_files_consistent() -> None:
    def keys(d: dict, prefix: str = "") -> set[str]:  # type: ignore[type-arg]
        out: set[str] = set()
        for k, v in d.items():
            if isinstance(v, dict):
                out |= keys(v, f"{prefix}{k}.")
            else:
                out.add(prefix + k)
        return out

    files = {
        lang: json.loads((STATIC / "i18n" / f"{lang}.json").read_text())
        for lang in ("pl", "en", "uk")
    }
    base = {k for k in keys(files["pl"]) if not k.startswith("plurals.")}
    for lang, data in files.items():
        assert {k for k in keys(data) if not k.startswith("plurals.")} == base, lang
    # kody uzasadnień i ostrzeżeń z backendu muszą mieć tłumaczenia
    import tomllib

    from termin.core import ranking  # noqa: F401 - upewniamy się, że moduł się importuje

    backend = tomllib.loads((STATIC.parents[1] / "locales" / "pl.toml").read_text())
    for section in ("reasons", "alerts"):
        assert set(backend[section]) == set(files["pl"][section]), section


def test_bundle_size_budget() -> None:
    own = [
        STATIC / "index.html",
        STATIC / "app.js",
        STATIC / "style.css",
        STATIC / "i18n" / "pl.json",
    ]
    total = sum(p.stat().st_size for p in own)
    assert total < 150_000, f"pakiet własny {total} B przekracza budżet 150 KB"


def test_healthz_and_metrics(client: TestClient) -> None:
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    client.get("/api/search", params={"q": "okulista dorośli", "where": "Gdynia"})
    client.get("/api/history/abc")
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    text = metrics.text
    for name in (
        "termin_http_requests_total",
        "termin_nfz_requests_total",
        "termin_cache_lookups_total",
        "termin_nfz_request_seconds",
        "termin_http_request_seconds",
    ):
        assert name in text, name
    assert 'route="/api/history/{queue_id}"' in text  # szablon, nie konkretne ID
    assert "abc" not in text


def test_healthz_reports_db_failure(client: TestClient) -> None:
    client.app.state.runtime.conn.close()  # type: ignore[attr-defined]
    assert client.get("/healthz").status_code == 503


def test_rate_limit(tmp_path: Path, fake: FakeNfz) -> None:
    settings = Settings(data_dir=tmp_path, geocoder="offline", rate_limit="3/minute")
    app = create_app(settings, transports=Transports(nfz=fake.transport()), today=lambda: TODAY)
    with TestClient(app) as c:
        params = {"q": "okulista dorośli", "where": "Gdynia"}
        codes = [c.get("/api/search", params=params).status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200]
        assert codes[3:] == [429, 429]
        r = c.get("/api/search", params=params)
        assert r.json()["code"] == "rate"
        assert r.headers["Retry-After"] == "60"
        assert c.get("/api/suggest", params={"q": "okul"}).status_code == 200  # osobny limit
        assert "termin_rate_limited_total" in c.get("/metrics").text
