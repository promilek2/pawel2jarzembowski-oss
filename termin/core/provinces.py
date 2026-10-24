"""Oddziały wojewódzkie NFZ (kody 01–16) i przybliżona geometria województw.

Kody NFZ są alfabetyczne i różnią się od TERYT: TERYT = 2 × kod NFZ
(np. pomorskie: NFZ 11, TERYT 22).

Bounding boxy są przybliżone i celowo zawyżone (margines `_PAD`). Służą tylko do
wyboru województw do odpytania przy wyszukiwaniu w promieniu, więc lepiej odpytać
o jedno województwo za dużo niż za mało.
"""

from __future__ import annotations

from dataclasses import dataclass

from termin.core.geo_math import haversine_km
from termin.core.text import fold


@dataclass(frozen=True, slots=True)
class Province:
    code: str
    name: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    @property
    def teryt(self) -> str:
        return f"{int(self.code) * 2:02d}"

    def bbox_distance_km(self, lat: float, lon: float) -> float:
        """Odległość punktu od (zawyżonego) prostokąta województwa; 0 gdy w środku."""
        clat = min(max(lat, self.lat_min - _PAD), self.lat_max + _PAD)
        clon = min(max(lon, self.lon_min - _PAD), self.lon_max + _PAD)
        return haversine_km(lat, lon, clat, clon)


_PAD = 0.15

PROVINCES: dict[str, Province] = {
    p.code: p
    for p in (
        Province("01", "dolnośląskie", 50.09, 51.81, 14.81, 17.80),
        Province("02", "kujawsko-pomorskie", 52.33, 53.78, 17.24, 19.78),
        Province("03", "lubelskie", 50.25, 52.29, 21.61, 24.15),
        Province("04", "lubuskie", 51.36, 53.12, 14.53, 16.42),
        Province("05", "łódzkie", 50.84, 52.40, 18.07, 20.66),
        Province("06", "małopolskie", 49.17, 50.52, 19.08, 21.43),
        Province("07", "mazowieckie", 51.01, 53.48, 19.26, 23.13),
        Province("08", "opolskie", 49.97, 51.20, 16.90, 18.70),
        Province("09", "podkarpackie", 49.00, 50.82, 21.14, 23.55),
        Province("10", "podlaskie", 52.28, 54.41, 21.58, 23.95),
        Province("11", "pomorskie", 53.49, 54.84, 16.70, 19.65),
        Province("12", "śląskie", 49.39, 51.10, 18.03, 19.98),
        Province("13", "świętokrzyskie", 50.17, 51.35, 19.70, 21.87),
        Province("14", "warmińsko-mazurskie", 53.13, 54.46, 19.13, 22.89),
        Province("15", "wielkopolskie", 51.10, 53.66, 15.78, 19.08),
        Province("16", "zachodniopomorskie", 52.62, 54.57, 14.12, 16.98),
    )
}

_BY_NAME = {p.name: p.code for p in PROVINCES.values()}


_BY_FOLDED = {fold(name): code for name, code in _BY_NAME.items()}


def province_code(value: str) -> str | None:
    """Zwraca kod NFZ dla kodu ('7', '07') lub nazwy ('Mazowieckie', 'województwo pomorskie')."""
    v = value.strip()
    if v.isdigit():
        code = f"{int(v):02d}"
        return code if code in PROVINCES else None
    folded = fold(v).removeprefix("wojewodztwo ").strip()
    return _BY_FOLDED.get(folded)


def province_from_teryt(teryt: str | None) -> str | None:
    """Kod NFZ z kodu TERYT (pierwsze dwie cyfry to województwo)."""
    if not teryt or len(teryt) < 2 or not teryt[:2].isdigit():
        return None
    num = int(teryt[:2])
    if num % 2:
        return None
    code = f"{num // 2:02d}"
    return code if code in PROVINCES else None


def provinces_within(lat: float, lon: float, radius_km: float) -> list[str]:
    """Kody województw, których (zawyżony) bbox przecina okrąg o promieniu `radius_km`."""
    return sorted(
        code for code, p in PROVINCES.items() if p.bbox_distance_km(lat, lon) <= radius_km
    )
