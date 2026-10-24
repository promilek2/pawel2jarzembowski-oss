from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from termin.core.models import QueueEntry, to_date, to_flag, to_float, to_int
from termin.core.parser import (
    ApiFormatError,
    parse_error,
    parse_many_places,
    parse_queue,
    parse_queues,
    parse_string_dictionary,
)
from tests.fake_nfz import FIXTURES, load_fixture


def _by_id(entries: tuple[QueueEntry, ...], idx: int) -> QueueEntry:
    suffix = f"{idx:04d}"
    return next(e for e in entries if e.id.endswith(suffix))


@pytest.fixture
def page1() -> tuple[QueueEntry, ...]:
    return parse_queues(load_fixture("queues_okulistyczna_11_p1.json")).entries


def test_parse_page_meta() -> None:
    page = parse_queues(load_fixture("queues_okulistyczna_11_p1.json"))
    assert page.count == 30
    assert page.has_next is True
    assert len(page.entries) == 25
    assert page.skipped == 0
    last = parse_queues(load_fixture("queues_okulistyczna_11_p2.json"))
    assert last.has_next is False


def test_basic_fields(page1: tuple[QueueEntry, ...]) -> None:
    e = _by_id(page1, 1)
    assert e.benefit == "PORADNIA OKULISTYCZNA"
    assert e.locality == "GDYNIA"
    assert e.first_date == dt.date(2026, 11, 3)
    assert e.as_at == dt.date(2026, 10, 21)
    assert e.has_coords
    assert e.awaiting == 137
    assert e.average_period == 35
    assert e.toilet is True
    assert e.many_places is False
    assert e.case == 1


def test_null_dates(page1: tuple[QueueEntry, ...]) -> None:
    no_date = _by_id(page1, 2)
    assert no_date.first_date is None
    assert no_date.as_at == dt.date(2026, 10, 20)
    null_dates = _by_id(page1, 11)
    assert null_dates.dates is None
    assert null_dates.first_date is None
    assert null_dates.as_at is None


def test_zero_null_and_foreign_coords_become_none(page1: tuple[QueueEntry, ...]) -> None:
    for idx in (3, 4, 14):
        e = _by_id(page1, idx)
        assert e.latitude is None and e.longitude is None, idx
        assert not e.has_coords


def test_comma_coords_are_parsed(page1: tuple[QueueEntry, ...]) -> None:
    e = _by_id(page1, 12)
    assert e.latitude == pytest.approx(54.52)
    assert e.longitude == pytest.approx(18.54)


def test_empty_phone_is_none(page1: tuple[QueueEntry, ...]) -> None:
    assert _by_id(page1, 5).phone is None


def test_statistics_variants(page1: tuple[QueueEntry, ...]) -> None:
    assert _by_id(page1, 8).statistics is None
    assert _by_id(page1, 8).average_period is None
    computed = _by_id(page1, 9)
    assert computed.awaiting is None
    assert computed.average_period == 41


def test_children_and_age_range(page1: tuple[QueueEntry, ...]) -> None:
    kid = _by_id(page1, 13)
    assert kid.is_for_children
    assert kid.age_range == "0-18"
    dict_range = _by_id(page1, 6 * 3)  # co 6. pozycja jest dla dzieci, age-range jako obiekt
    assert dict_range.is_for_children
    assert dict_range.age_range == '{"from": 0, "to": 18}'
    assert not _by_id(page1, 1).is_for_children


def test_many_places_flag(page1: tuple[QueueEntry, ...]) -> None:
    assert _by_id(page1, 10).many_places is True


def test_bad_items_are_skipped_not_fatal() -> None:
    payload = load_fixture("queues_okulistyczna_11_p2.json")
    payload["data"].extend([None, "x", {"type": "queues"}, {"id": "", "attributes": {}}])
    page = parse_queues(payload)
    assert len(page.entries) == 5
    assert page.skipped == 4


def test_data_null_and_wrong_shapes() -> None:
    assert parse_queues({"data": None}).entries == ()
    with pytest.raises(ApiFormatError):
        parse_queues([])
    with pytest.raises(ApiFormatError):
        parse_queues({"data": {}})


def test_single_queue() -> None:
    entry = parse_queue(load_fixture("queue_single.json"))
    assert entry is not None
    assert entry.locality == "GDYNIA"
    assert parse_queue({"data": []}) is None


def test_many_places_recursive() -> None:
    locations = parse_many_places(load_fixture("many_places.json"))
    assert [loc.locality for loc in locations] == ["GDYNIA", "LĘBORK"]
    assert locations[0].has_coords
    assert not locations[1].has_coords  # 0,0 → brak
    # inny możliwy kształt: obiekt z listą miejsc w atrybutach
    nested = {"data": {"attributes": {"places": [{"address": "A 1", "locality": "SOPOT"}]}}}
    assert parse_many_places(nested)[0].locality == "SOPOT"


def test_dictionaries() -> None:
    names, has_next = parse_string_dictionary(load_fixture("benefits_okul.json"))
    assert "PORADNIA OKULISTYCZNA" in names
    assert has_next is False
    objects = {"data": [{"attributes": {"name": "X"}}, {"name": "Y"}, 5], "links": {"next": "n"}}
    assert parse_string_dictionary(objects) == (["X", "Y"], True)


def test_parse_error() -> None:
    assert parse_error(load_fixture("error_400.json")) == (
        "Nieprawidłowa wartość parametru province"
    )
    assert parse_error({}) is None
    assert parse_error("x") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Y", True), ("n", False), ("TAK", True), (True, True), (None, None), ("?", None)],
)
def test_to_flag(raw: object, expected: bool | None) -> None:
    assert to_flag(raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-11-03", dt.date(2026, 11, 3)),
        ("2026-11-03T00:00:00+01:00", dt.date(2026, 11, 3)),
        ("03.11.2026", dt.date(2026, 11, 3)),
        ("", None),
        ("jutro", None),
        (12, None),
        (None, None),
    ],
)
def test_to_date(raw: object, expected: dt.date | None) -> None:
    assert to_date(raw) == expected


def test_numbers() -> None:
    assert to_float("54,1") == pytest.approx(54.1)
    assert to_float(0) is None
    assert to_float("abc") is None
    assert to_float(float("nan")) is None
    assert to_float(True) is None
    assert to_int(0) == 0
    assert to_int("0") == 0
    assert to_int("12.6") == 13
    assert to_int(None) is None


def test_live_fixtures_parse_if_present() -> None:
    """Kontrakt: prawdziwe odpowiedzi z scripts/capture_fixtures.py też muszą się parsować."""
    live = FIXTURES / "live"
    files = sorted(live.glob("queues_*.json")) if live.exists() else []
    if not files:
        pytest.skip("brak przechwyconych odpowiedzi (scripts/capture_fixtures.py)")
    for path in files:
        page = parse_queues(load_fixture(str(Path("live") / path.name)))
        assert page.skipped == 0, path.name
