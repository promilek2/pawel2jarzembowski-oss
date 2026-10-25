"""Modele domenowe (Pydantic v2).

Modele odpowiadające danym NFZ używają aliasów w kebab-case (tak jak API), a
walidatory normalizują dziwactwa danych: flagi "Y"/"N", współrzędne 0 lub null,
puste stringi, daty w różnych formatach. Patrz ADR-0001.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from enum import IntEnum, StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from termin.core.geo_math import in_poland
from termin.core.text import parse_pcus


def _kebab(name: str) -> str:
    return name.replace("_", "-")


class NfzModel(BaseModel):
    """Baza modeli danych NFZ: aliasy kebab-case, ignorowanie nieznanych pól."""

    model_config = ConfigDict(
        alias_generator=_kebab,
        populate_by_name=True,
        extra="ignore",
        frozen=True,
    )


# --------------------------------------------------------------------------- normalizacja


def to_flag(value: Any) -> bool | None:
    """'Y'/'T'/'TAK'/True → True, 'N'/'NIE'/False → False, reszta → None."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().upper()
        if v in {"Y", "T", "TAK", "TRUE", "1"}:
            return True
        if v in {"N", "NIE", "FALSE", "0"}:
            return False
    return None


def to_float(value: Any) -> float | None:
    """Liczba albo None. 0, '', '0,0' i wartości nienumeryczne → None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if not value:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number == 0.0:  # NaN albo 0
        return None
    return number


def to_int(value: Any) -> int | None:
    number = to_float(value)
    if number is None:
        # 0 to sensowna liczba oczekujących, więc obsługujemy ją osobno
        if isinstance(value, int | float) and not isinstance(value, bool) and value == 0:
            return 0
        if isinstance(value, str) and value.strip() in {"0", "0.0"}:
            return 0
        return None
    return round(number)


_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d", "%d-%m-%Y")


def to_date(value: Any) -> dt.date | None:
    """Data z ISO, ISO z czasem albo formatu polskiego. Śmieci → None."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}[T ]", v):
        v = v[:10]
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(v, fmt).date()  # noqa: DTZ007 - sama data, bez strefy
        except ValueError:
            continue
    return None


def to_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True) if value else None
    text = str(value).strip()
    return text or None


# --------------------------------------------------------------------------- dane NFZ


class Case(IntEnum):
    """Kategoria medyczna: przypadek stabilny albo pilny."""

    STABLE = 1
    URGENT = 2


class Dates(NfzModel):
    applicable: bool | None = None
    date: dt.date | None = None
    """Pierwszy wolny termin (API 1.3; od 1.07.2026 NFZ go nie publikuje)."""
    pcus: str | None = None
    """Prognozowany czas oczekiwania NFZ (PCUŚ, API 1.4), tekst, np. „1 mies. 3 tyg.”."""
    date_situation_as_at: dt.date | None = None
    """Stan na dzień: kiedy placówka zaktualizowała dane albo NFZ policzył PCUŚ."""

    @field_validator("pcus", mode="before")
    @classmethod
    def _pcus(cls, v: Any) -> str | None:
        return to_str(v)

    @property
    def pcus_days(self) -> int | None:
        return parse_pcus(self.pcus)

    @field_validator("applicable", mode="before")
    @classmethod
    def _flag(cls, v: Any) -> bool | None:
        return to_flag(v)

    @field_validator("date", "date_situation_as_at", mode="before")
    @classmethod
    def _date(cls, v: Any) -> dt.date | None:
        return to_date(v)


class ProviderStats(NfzModel):
    awaiting: int | None = None
    """Liczba oczekujących."""
    removed: int | None = None
    average_period: int | None = None
    """Średni czas oczekiwania w dniach (wg placówki)."""
    update: str | None = None

    @field_validator("awaiting", "removed", "average_period", mode="before")
    @classmethod
    def _int(cls, v: Any) -> int | None:
        return to_int(v)

    @field_validator("update", mode="before")
    @classmethod
    def _str(cls, v: Any) -> str | None:
        return to_str(v)


class ComputedStats(NfzModel):
    average_period: int | None = None
    update: str | None = None

    @field_validator("average_period", mode="before")
    @classmethod
    def _int(cls, v: Any) -> int | None:
        return to_int(v)

    @field_validator("update", mode="before")
    @classmethod
    def _str(cls, v: Any) -> str | None:
        return to_str(v)


class Statistics(NfzModel):
    provider_data: ProviderStats | None = None
    computed_data: ComputedStats | None = None

    @field_validator("provider_data", "computed_data", mode="before")
    @classmethod
    def _dict_or_none(cls, v: Any) -> Any:
        return v if isinstance(v, dict) else None


class Location(NfzModel):
    """Fizyczna lokalizacja (główna albo jedna z wielu lokalizacji placówki)."""

    place: str | None = None
    address: str | None = None
    locality: str | None = None
    phone: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    @field_validator("place", "address", "locality", "phone", mode="before")
    @classmethod
    def _str(cls, v: Any) -> str | None:
        return to_str(v)

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def _coord(cls, v: Any) -> float | None:
        return to_float(v)

    @model_validator(mode="before")
    @classmethod
    def _coords_in_poland(cls, data: Any) -> Any:
        """Współrzędne tylko parami i tylko w Polsce; inaczej obie None."""
        if not isinstance(data, dict):
            return data
        lat = to_float(data.get("latitude"))
        lon = to_float(data.get("longitude"))
        if lat is not None and lon is not None and not in_poland(lat, lon) and in_poland(lon, lat):
            lat, lon = lon, lat  # w API 1.4 część rekordów ma zamienioną szerokość z długością
        if lat is None or lon is None or not in_poland(lat, lon):
            lat = lon = None
        return {**data, "latitude": lat, "longitude": lon}

    @property
    def has_coords(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class QueueEntry(Location):
    """Jedna pozycja z `/queues`: świadczenie w konkretnej placówce."""

    id: str
    case: int | None = None
    benefit: str = ""
    provider: str | None = None
    provider_code: str | None = None
    teryt_place: str | None = None
    many_places: bool = False
    benefits_for_children: bool | None = None
    age_range: str | None = None
    toilet: bool | None = None
    ramp: bool | None = None
    car_park: bool | None = None
    elevator: bool | None = None
    wheelchairs: bool | None = None
    """Placówka udostępnia wózki inwalidzkie (API 1.4)."""
    automatic_door: bool | None = None
    public_transport_lines: str | None = None
    """Opis dojazdu komunikacją, np. „ZNAJDUJE SIĘ PRZYSTANEK KOMUNIKACJI MIEJSKIEJ - 2,3”."""
    queue_in_cer: bool | None = None
    """Kolejka w Centralnej e-Rejestracji (API 1.4): zapisy przez IKP, bez PCUŚ i statystyk."""
    dates: Dates | None = None
    statistics: Statistics | None = None
    extra_locations: tuple[Location, ...] = ()
    """Lokalizacje z `/many-places/{id}` (uzupełniane przez wyszukiwarkę)."""
    location_approx: bool = False
    """Współrzędne przybliżone (środek gminy z TERYT), bo NFZ ich nie podał."""

    @field_validator(
        "provider",
        "provider_code",
        "teryt_place",
        "age_range",
        "benefit",
        "public_transport_lines",
        mode="before",
    )
    @classmethod
    def _str_fields(cls, v: Any) -> str | None:
        return to_str(v)

    @field_validator("benefit", mode="after")
    @classmethod
    def _benefit_default(cls, v: str | None) -> str:
        return v or ""

    @field_validator("many_places", mode="before")
    @classmethod
    def _many(cls, v: Any) -> bool:
        return bool(to_flag(v))

    @field_validator(
        "benefits_for_children",
        "toilet",
        "ramp",
        "car_park",
        "elevator",
        "queue_in_cer",
        "wheelchairs",
        "automatic_door",
        mode="before",
    )
    @classmethod
    def _flags(cls, v: Any) -> bool | None:
        return to_flag(v)

    @field_validator("case", mode="before")
    @classmethod
    def _case(cls, v: Any) -> int | None:
        return to_int(v)

    @field_validator("dates", "statistics", mode="before")
    @classmethod
    def _dict_or_none(cls, v: Any) -> Any:
        return v if isinstance(v, dict) else None

    # --- wygodne skróty ---

    @property
    def first_date(self) -> dt.date | None:
        """Pierwszy wolny termin (API 1.3). Od API 1.4 jest prognoza: `expected_date`."""
        return self.dates.date if self.dates else None

    def expected_date(self, today: dt.date) -> dt.date | None:
        """Pierwszy wolny termin albo orientacyjna data z prognozy NFZ: dziś + PCUŚ.

        PCUŚ to prognozowany czas oczekiwania dla kogoś, kto zapisuje się teraz, więc
        liczymy od dzisiaj (nie od „stanu na”). To nie jest wolny termin (`is_forecast`).
        """
        if self.first_date is not None:
            return self.first_date
        days = self.dates.pcus_days if self.dates else None
        return today + dt.timedelta(days=days) if days is not None else None

    @property
    def is_forecast(self) -> bool:
        return bool(self.dates and self.dates.date is None and self.dates.pcus_days is not None)

    @property
    def pcus(self) -> str | None:
        return self.dates.pcus if self.dates else None

    @property
    def in_cer(self) -> bool:
        return bool(self.queue_in_cer)

    @property
    def barrier_free(self) -> bool:
        """Da się wejść bez schodów: podjazd albo winda (wg danych placówki dla NFZ)."""
        return bool(self.ramp or self.elevator)

    @property
    def as_at(self) -> dt.date | None:
        return self.dates.date_situation_as_at if self.dates else None

    @property
    def awaiting(self) -> int | None:
        s = self.statistics
        return s.provider_data.awaiting if s and s.provider_data else None

    @property
    def average_period(self) -> int | None:
        s = self.statistics
        if s and s.provider_data and s.provider_data.average_period is not None:
            return s.provider_data.average_period
        if s and s.computed_data:
            return s.computed_data.average_period
        return None

    @property
    def is_for_children(self) -> bool:
        if self.benefits_for_children:
            return True
        return "DLA DZIECI" in self.benefit.upper()

    @property
    def all_locations(self) -> tuple[Location, ...]:
        main = Location(
            place=self.place,
            address=self.address,
            locality=self.locality,
            phone=self.phone,
            latitude=self.latitude,
            longitude=self.longitude,
        )
        return (main, *self.extra_locations)


class Page(BaseModel):
    """Strona wyników `/queues` (po sparsowaniu)."""

    model_config = ConfigDict(frozen=True)

    count: int | None = None
    entries: tuple[QueueEntry, ...] = ()
    has_next: bool = False
    skipped: int = 0
    """Ile rekordów pominięto, bo nie dało się ich sparsować."""


# --------------------------------------------------------------------------- wyszukiwanie


class SortMode(StrEnum):
    FASTEST = "najszybciej"
    NEAREST = "najblizej"
    BALANCED = "kompromis"


class GeoPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    lat: float
    lon: float
    label: str | None = None
    province: str | None = None
    """Kod NFZ województwa, jeśli wiadomo."""
    source: str = "unknown"


class Reason(BaseModel):
    """Element wyjaśnienia „dlaczego tu” (kod + parametry, tłumaczone w UI)."""

    model_config = ConfigDict(frozen=True)

    code: str
    params: dict[str, int | float | str] = Field(default_factory=dict)


class Alert(BaseModel):
    """Ostrzeżenie o jakości danych (kod + parametry, tłumaczone w UI)."""

    model_config = ConfigDict(frozen=True)

    code: str
    params: dict[str, int | float | str] = Field(default_factory=dict)


class RankedResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    entry: QueueEntry
    location: Location
    """Lokalizacja najbliższa użytkownikowi (dla many-places)."""
    distance_km: float | None = None
    travel_min: float | None = None
    days_until: int | None = None
    expected_date: dt.date | None = None
    """Pierwszy wolny termin albo dziś + prognoza NFZ (PCUŚ), patrz `entry.is_forecast`."""
    score: float | None = None
    province: str | None = None
    reasons: tuple[Reason, ...] = ()
    alerts: tuple[Alert, ...] = ()
    explanation: str = ""
