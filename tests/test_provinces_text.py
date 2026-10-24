from __future__ import annotations

import pytest

from termin.core.geo_math import haversine_km, in_poland
from termin.core.provinces import (
    PROVINCES,
    province_code,
    province_from_teryt,
    provinces_within,
)
from termin.core.text import fold, plural_pl


def test_sixteen_provinces_with_teryt() -> None:
    assert len(PROVINCES) == 16
    assert PROVINCES["11"].name == "pomorskie"
    assert PROVINCES["11"].teryt == "22"
    assert PROVINCES["01"].teryt == "02"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("7", "07"),
        ("07", "07"),
        ("Mazowieckie", "07"),
        ("województwo pomorskie", "11"),
        ("SLASKIE", "12"),
        ("Łódzkie", "05"),
        ("kujawsko-pomorskie", "02"),
        ("17", None),
        ("Bawaria", None),
    ],
)
def test_province_code(raw: str, code: str | None) -> None:
    assert province_code(raw) == code


def test_province_from_teryt() -> None:
    assert province_from_teryt("2262011") == "11"
    assert province_from_teryt("1465011") == "07"
    assert province_from_teryt("23") is None
    assert province_from_teryt(None) is None
    assert province_from_teryt("ab") is None


def test_provinces_within_radius() -> None:
    gdynia = (54.5189, 18.5305)
    near = provinces_within(*gdynia, 30)
    assert "11" in near
    assert len(near) <= 2  # bbox-y są zawyżone: może dojść sąsiednie województwo
    assert provinces_within(*gdynia, 10) == ["11"]
    wide = provinces_within(*gdynia, 150)
    assert {"11", "02", "14"} <= set(wide)
    # Szczecin, mały promień → tylko zachodniopomorskie
    assert provinces_within(53.428, 14.553, 10) == ["16"]
    # Łódź leży w zawyżonym bbox-ie mazowieckiego: odpytamy oba (ostrożnie)
    assert "05" in provinces_within(51.759, 19.456, 10)


def test_haversine() -> None:
    # Gdynia – Gdańsk ≈ 20,6 km
    assert haversine_km(54.5189, 18.5305, 54.3520, 18.6466) == pytest.approx(20, abs=1.5)
    assert haversine_km(52.0, 21.0, 52.0, 21.0) == 0
    assert in_poland(52.2, 21.0)
    assert not in_poland(48.0, 2.0)


def test_fold_and_plural() -> None:
    assert fold("Zaćma  ŁÓDŹ!") == "zacma lodz"
    assert fold("Poradnia Chirurgii Urazowo-Ortopedycznej") == (
        "poradnia chirurgii urazowo-ortopedycznej"
    )
    assert [plural_pl(n, "dzień", "dni", "dni") for n in (1, 2, 5)] == ["dzień", "dni", "dni"]
    forms = [plural_pl(n, "osoba", "osoby", "osób") for n in (1, 3, 12, 22, 25)]
    assert forms == ["osoba", "osoby", "osób", "osoby", "osób"]
