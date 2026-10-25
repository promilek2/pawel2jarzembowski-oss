from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sqlite3
from collections.abc import Iterator

import pytest
from typer.testing import CliRunner

from termin.core.models import QueueEntry
from termin.core.parser import parse_queues
from termin.runtime import Runtime
from termin.store.db import connect
from termin.store.snapshots import (
    HistoryPoint,
    SnapshotStore,
    compute_trend,
    percentile,
    to_csv,
    to_json,
)
from tests.fake_nfz import load_fixture

QUERY = "ŚWIADCZENIA Z ZAKRESU OKULISTYKI"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def entries() -> list[QueueEntry]:
    p1 = parse_queues(load_fixture("queues_okulistyczna_11_p1.json")).entries
    p2 = parse_queues(load_fixture("queues_okulistyczna_11_p2.json")).entries
    return [*p1, *p2]


def at(day: str) -> dt.datetime:
    return dt.datetime.fromisoformat(f"{day}T08:00:00+00:00")


def shifted(entry: QueueEntry, first: str) -> QueueEntry:
    """Kopia pozycji z innym pierwszym terminem."""
    assert entry.dates is not None
    dates = entry.dates.model_copy(update={"date": dt.date.fromisoformat(first)})
    return entry.model_copy(update={"dates": dates})


def test_save_replaces_same_day(conn: sqlite3.Connection, entries: list[QueueEntry]) -> None:
    store = SnapshotStore(conn)
    store.save(query=QUERY, province="11", case_no=1, entries=entries, now=at("2026-10-24"))
    store.save(query=QUERY, province="11", case_no=1, entries=entries[:5], now=at("2026-10-24"))
    assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM snapshot_rows").fetchone()[0] == 5
    store.save(query=QUERY, province="11", case_no=1, entries=entries, now=at("2026-10-25"))
    assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2


def test_no_user_data_in_snapshot_tables(conn: sqlite3.Connection) -> None:
    columns = {
        row[1]
        for table in ("snapshots", "snapshot_rows")
        for row in conn.execute(f"PRAGMA table_info({table})")
    }
    assert not columns & {"user_lat", "user_lon", "where", "ip", "email", "radius"}


def test_history_and_trend(conn: sqlite3.Connection, entries: list[QueueEntry]) -> None:
    store = SnapshotStore(conn)
    e = entries[0]  # pierwszy termin 2026-11-03
    days = ["2026-09-01", "2026-09-15", "2026-09-29", "2026-10-13", "2026-10-24"]
    firsts = ["2026-12-31", "2026-12-20", "2026-12-01", "2026-11-15", "2026-11-03"]
    for day, first in zip(days, firsts, strict=True):
        store.save(query=QUERY, province="11", case_no=1, entries=[shifted(e, first)], now=at(day))
    history = store.history(e.id)
    assert [p.taken_on.isoformat() for p in history] == days
    assert history[0].wait_days == 121
    assert history[-1].wait_days == 10
    trend = store.trend(e.id, days=None)
    assert trend.direction == "shorter"
    assert trend.change_days == -111
    assert trend.slope_days_per_week is not None and trend.slope_days_per_week < -1
    recent = store.trend(e.id, days=20)
    assert len(recent.points) == 2
    data = trend.to_dict()
    assert data["points"][0]["wait_days"] == 121


def test_compute_trend_edge_cases() -> None:
    d = dt.date(2026, 10, 1)
    assert compute_trend([]).direction == "unknown"
    one = [HistoryPoint(d, dt.date(2026, 11, 1), None, None)]
    assert compute_trend(one).direction == "unknown"
    stable = [
        HistoryPoint(d, dt.date(2026, 11, 1), None, None),
        HistoryPoint(d + dt.timedelta(days=14), dt.date(2026, 11, 15), None, None),
    ]
    assert compute_trend(stable).direction == "stable"
    longer = [
        HistoryPoint(d, dt.date(2026, 11, 1), None, None),
        HistoryPoint(d + dt.timedelta(days=7), dt.date(2026, 12, 1), None, None),
    ]
    assert compute_trend(longer).direction == "longer"


def test_wait_stats(conn: sqlite3.Connection, entries: list[QueueEntry]) -> None:
    store = SnapshotStore(conn)
    store.save(query=QUERY, province="11", case_no=1, entries=entries, now=at("2026-10-24"))
    stats = store.wait_stats(QUERY, "11", 1)
    assert stats.n == 38
    # bez terminu (2 + kolejka w CeR), z terminem w przeszłości (1) i pustej kolejki
    # (PCUŚ „0 dni”, nikt nie czeka) nie liczymy; prognozy PCUŚ = dzień zapisu + prognoza
    assert stats.with_date == 33
    assert stats.median is not None
    assert stats.p25 is not None and stats.p75 is not None and stats.p90 is not None
    assert stats.minimum is not None and stats.maximum is not None
    assert stats.minimum <= stats.p25 <= stats.median <= stats.p75 <= stats.p90 <= stats.maximum
    teryts = {d.teryt for d in stats.districts}
    assert "2262" in teryts  # Gdynia
    gdynia = next(d for d in stats.districts if d.teryt == "2262")
    assert "GDYNIA" in gdynia.localities
    assert gdynia.lat is not None
    kids = store.wait_stats(QUERY, "11", 1, for_children=True)
    assert kids.n == 5
    empty = store.wait_stats("XYZ", "11", 1)
    assert empty.n == 0 and empty.median is None
    assert store.wait_stats(QUERY, "11", 1, on=dt.date(2026, 1, 1)).taken_on is None


def test_percentile() -> None:
    assert percentile([5], 0.9) == 5
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([10, 20, 30, 40, 50], 0.9) == 46


def test_export_and_purge(conn: sqlite3.Connection, entries: list[QueueEntry]) -> None:
    store = SnapshotStore(conn)
    store.save(query=QUERY, province="11", case_no=1, entries=entries, now=at("2026-01-10"))
    store.save(query=QUERY, province="11", case_no=1, entries=entries, now=at("2026-10-24"))
    rows = store.export_rows(query=QUERY, since=dt.date(2026, 10, 1))
    assert len(rows) == 38
    assert rows[0]["taken_on"] == "2026-10-24"
    assert isinstance(rows[0]["for_children"], bool)
    parsed = list(csv.DictReader(io.StringIO(to_csv(rows))))
    assert len(parsed) == 38
    assert "wait_days" in parsed[0]
    payload = json.loads(to_json(rows))
    assert payload["meta"]["count"] == 38
    assert store.export_rows(province="07") == []
    assert len(store.export_rows(until=dt.date(2026, 2, 1))) == 38
    assert store.purge(180, today=dt.date(2026, 10, 24)) == 1
    assert len(store.export_rows()) == 38


async def test_search_saves_snapshot(runtime: Runtime) -> None:
    from termin.core.benefits import Variant
    from termin.core.search import SearchRequest

    await runtime.search.search(
        SearchRequest(text="okulista", variant=Variant.ADULT, where="Gdynia", radius_km=20)
    )
    rows = runtime.snapshots.export_rows()
    # snapshot zawiera surowe wyniki NFZ dla fragmentu (także wariant dla dzieci)
    assert len(rows) == 37
    assert rows[0]["query"] == "ŚWIADCZENIA Z ZAKRESU OKULISTYKI"


def test_cli_stats_history_export(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from termin.cli import commands as cli
    from termin.config import Settings
    from termin.runtime import Transports
    from tests.conftest import TODAY
    from tests.fake_nfz import FakeNfz

    fake = FakeNfz.with_defaults()
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(
        cli,
        "make_runtime",
        lambda s: Runtime(s, transports=Transports(nfz=fake.transport()), today=lambda: TODAY),
    )
    runner = CliRunner()
    stats = runner.invoke(cli.app, ["statystyki", "okulista", "-w", "pomorskie"])
    assert stats.exit_code == 0, stats.output
    assert "mediana" in stats.output
    stats_json = runner.invoke(cli.app, ["statystyki", "okulista", "-w", "11", "--json"])
    assert json.loads(stats_json.stdout)["n"] == 37
    out = tmp_path / "e.csv"
    exp = runner.invoke(cli.app, ["eksport", "-s", "okulista", "-o", str(out)])
    assert exp.exit_code == 0, exp.output
    assert out.read_text().startswith("taken_on,query")
    exp_json = runner.invoke(cli.app, ["eksport", "-f", "json"])
    assert json.loads(exp_json.stdout)["meta"]["count"] >= 30
    assert runner.invoke(cli.app, ["eksport", "--od", "wczoraj"]).exit_code == 2
    hist = runner.invoke(cli.app, ["historia", "00000001-0000-4000-8000-000000000001"])
    assert hist.exit_code == 0
    assert "Za mało danych" in hist.output
    none = runner.invoke(cli.app, ["historia", "brak"])
    assert "Brak historii" in none.output
    hist_json = runner.invoke(cli.app, ["historia", "brak", "--json"])
    assert json.loads(hist_json.stdout)["direction"] == "unknown"
    snap = runner.invoke(cli.app, ["snapshot", "rezonans", "-w", "07"])
    assert snap.exit_code == 0, snap.output
    assert runner.invoke(cli.app, ["snapshot"]).exit_code == 2
    monkeypatch.setenv(
        "TERMIN_TRACKED_QUERIES", '["REZONANS@07", "BAD", "ŚWIADCZENIA Z ZAKRESU OKULISTYKI@11@2"]'
    )
    from termin.config import get_settings

    get_settings.cache_clear()
    assert runner.invoke(cli.app, ["snapshot"]).exit_code == 0
    assert Settings().tracked_queries[0] == "REZONANS@07"


def test_parse_tracked() -> None:
    from termin.cli.commands import parse_tracked

    assert parse_tracked(["rezonans@07", "x", "@11", "OKUL@pomorskie@2"]) == [
        ("REZONANS", "07", 1),
        ("OKUL", "11", 2),
    ]
