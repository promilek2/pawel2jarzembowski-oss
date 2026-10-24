"""Lokalizacja użytkownika: współrzędne, Nominatim (OSM) i wariant offline (ADR-0006).

Nominatim: maks. 1 żądanie na sekundę (blokada w procesie), własny User-Agent, cache w
SQLite. Offline: wbudowana lista miejscowości (`termin/data/places.csv`) i opcjonalna
tabela kodów pocztowych wygenerowana skryptem `scripts/build_postal_centroids.py`.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import re
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import httpx
from rapidfuzz import fuzz, process

from termin import USER_AGENT
from termin.config import Settings
from termin.core.geo_math import haversine_km, in_poland
from termin.core.models import GeoPoint
from termin.core.provinces import province_code
from termin.core.text import fold
from termin.logs import get_logger

log = get_logger(__name__)

_COORDS = re.compile(r"^\s*(-?\d{1,2}(?:[.,]\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:[.,]\d+)?)\s*$")
_POSTAL = re.compile(r"\b(\d{2})-?(\d{3})\b")
_STREET = re.compile(
    r"\b(ul|ulica|al|aleja|aleje|pl|plac|os|osiedle|rondo)\.?\s+[^,]*", re.IGNORECASE
)
NOMINATIM_MIN_INTERVAL_S = 1.0
FUZZY_LOCALITY_SCORE = 80.0


@dataclass(frozen=True, slots=True)
class Place:
    name: str
    province: str
    lat: float
    lon: float


def parse_coordinates(text: str) -> tuple[float, float] | None:
    """'54.52, 18.53' albo '54,52 18,53' → (lat, lon), jeśli w Polsce."""
    m = _COORDS.match(text)
    if not m:
        return None
    lat = float(m.group(1).replace(",", "."))
    lon = float(m.group(2).replace(",", "."))
    return (lat, lon) if in_poland(lat, lon) else None


def normalize_postal(text: str) -> str | None:
    m = _POSTAL.search(text)
    return f"{m.group(1)}-{m.group(2)}" if m else None


class Gazetteer:
    """Offline: miejscowości (wbudowane) + kody pocztowe (opcjonalny plik)."""

    def __init__(self, places: list[Place], postal: dict[str, Place] | None = None) -> None:
        self.places = places
        self.postal = postal or {}
        self._by_name: dict[str, Place] = {}
        for place in places:
            self._by_name.setdefault(fold(place.name), place)

    @classmethod
    def load(cls, postal_file: Path | None = None) -> Gazetteer:
        raw = resources.files("termin.data").joinpath("places.csv").read_text("utf-8")
        places = [
            Place(row["name"], row["province"], float(row["lat"]), float(row["lon"]))
            for row in csv.DictReader(io.StringIO(raw))
        ]
        postal: dict[str, Place] = {}
        if postal_file is not None and postal_file.exists():
            with postal_file.open(encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    postal[row["postal_code"]] = Place(
                        row["name"], row["province"], float(row["lat"]), float(row["lon"])
                    )
        return cls(places, postal)

    def find_locality(self, text: str) -> Place | None:
        """Szuka nazwy miejscowości w tekście adresu (od końca: „ul. X 1, 81-310 Gdynia”)."""
        cleaned = _STREET.sub(" ", _POSTAL.sub(" ", text))
        parts = [p for p in re.split(r"[,;\n]", cleaned) if p.strip()]
        for part in reversed(parts):
            words = fold(part).split()
            # najdłuższe dopasowanie: „ruda slaska”, „bielsko-biala”, „nowy sacz”
            for size in range(min(4, len(words)), 0, -1):
                for start in range(len(words) - size, -1, -1):
                    place = self._by_name.get(" ".join(words[start : start + size]))
                    if place is not None:
                        return place
        return self._fuzzy_locality(parts)

    def _fuzzy_locality(self, parts: list[str]) -> Place | None:
        """Odmiana i literówki: „w Gdyni”, „Krakowie”, „Wroclawiu” → miejscowość."""
        names = list(self._by_name)
        for part in reversed(parts):
            words = [w for w in fold(part).split() if len(w) >= 4 and not w.isdigit()]
            for size in (2, 1):
                for start in range(len(words) - size, -1, -1):
                    phrase = " ".join(words[start : start + size])
                    match = process.extractOne(
                        phrase, names, scorer=fuzz.ratio, score_cutoff=FUZZY_LOCALITY_SCORE
                    )
                    if match is not None:
                        return self._by_name[match[0]]
        return None

    def lookup(self, text: str) -> GeoPoint | None:
        code = normalize_postal(text)
        if code and code in self.postal:
            p = self.postal[code]
            return GeoPoint(
                lat=p.lat,
                lon=p.lon,
                label=f"{code} {p.name}",
                province=p.province,
                source="offline-postal",
            )
        place = self.find_locality(text)
        if place is not None:
            return GeoPoint(
                lat=place.lat,
                lon=place.lon,
                label=place.name,
                province=place.province,
                source="offline",
            )
        return None

    def nearest(self, lat: float, lon: float) -> Place:
        return min(self.places, key=lambda p: haversine_km(lat, lon, p.lat, p.lon))

    def province_for_point(self, lat: float, lon: float) -> str:
        """Województwo punktu wg najbliższej znanej miejscowości (przybliżenie offline)."""
        return self.nearest(lat, lon).province


Sleep = Callable[[float], Awaitable[None]]


class GeocodingError(RuntimeError):
    pass


class Geocoder:
    """Zamienia tekst (adres, kod pocztowy, współrzędne) na `GeoPoint`."""

    _slot_lock = threading.Lock()
    _next_slot = 0.0
    """Najwcześniejszy moment kolejnego żądania do Nominatim (wspólny dla procesu)."""

    def __init__(
        self,
        settings: Settings,
        *,
        conn: sqlite3.Connection | None = None,
        gazetteer: Gazetteer | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._conn = conn
        self.gazetteer = gazetteer or Gazetteer.load(settings.data_dir / "postal_codes.csv")
        self._sleep = sleep
        self._clock = clock
        self._http = httpx.AsyncClient(
            base_url=settings.nominatim_url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "pl"},
            timeout=10.0,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def geocode(self, text: str) -> GeoPoint | None:
        text = text.strip()
        if not text:
            return None
        coords = parse_coordinates(text)
        if coords is not None:
            return self.point(*coords, source="gps")

        mode = self._settings.geocoder
        if mode == "offline":
            return self.gazetteer.lookup(text)

        # Klucz cache to skrót (SHA-256) znormalizowanego adresu: w bazie nie ma adresów
        # w czytelnej postaci (PRIVACY.md).
        key = hashlib.sha256(fold(text).encode()).hexdigest()
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        try:
            point = await self._nominatim(text)
        except GeocodingError as exc:
            if mode == "nominatim":
                raise
            log.info("geocode.nominatim_unavailable", reason=str(exc))
            return self.gazetteer.lookup(text)
        if point is None:
            point = self.gazetteer.lookup(text) if mode == "auto" else None
        if point is not None and point.source == "nominatim":
            self._cache_put(key, point)
        return point

    def point(self, lat: float, lon: float, source: str = "gps") -> GeoPoint:
        """Punkt z GPS: województwo ustalamy offline (najbliższa miejscowość)."""
        return GeoPoint(
            lat=lat,
            lon=lon,
            label=None,
            province=self.gazetteer.province_for_point(lat, lon),
            source=source,
        )

    async def _throttle(self) -> None:
        """Rezerwuje kolejny wolny slot (co 1 s) i czeka na niego."""
        cls = type(self)
        with cls._slot_lock:
            now = self._clock()
            slot = max(now, cls._next_slot)
            cls._next_slot = slot + NOMINATIM_MIN_INTERVAL_S
        if slot > now:
            await self._sleep(slot - now)

    async def _nominatim(self, text: str) -> GeoPoint | None:
        await self._throttle()
        try:
            response = await self._http.get(
                "/search",
                params={
                    "q": text,
                    "format": "jsonv2",
                    "countrycodes": "pl",
                    "limit": "1",
                    "addressdetails": "1",
                },
            )
        except httpx.HTTPError as exc:
            raise GeocodingError(type(exc).__name__) from exc
        if response.status_code != 200:
            raise GeocodingError(f"HTTP {response.status_code}")
        try:
            results = response.json()
        except ValueError as exc:
            raise GeocodingError("niepoprawny JSON") from exc
        if not isinstance(results, list) or not results:
            return None
        top = results[0]
        try:
            lat, lon = float(top["lat"]), float(top["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        if not in_poland(lat, lon):
            return None
        address = top.get("address") or {}
        province = province_code(str(address.get("state", ""))) if address else None
        label = _short_label(address) or str(top.get("display_name", ""))[:80] or None
        return GeoPoint(
            lat=lat,
            lon=lon,
            label=label,
            province=province or self.gazetteer.province_for_point(lat, lon),
            source="nominatim",
        )

    def _cache_get(self, key: str) -> GeoPoint | None:
        if self._conn is None:
            return None
        row = self._conn.execute(
            "SELECT lat, lon, label, province FROM geocode_cache WHERE query = ?", (key,)
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return GeoPoint(lat=row[0], lon=row[1], label=row[2], province=row[3], source="cache")

    def _cache_put(self, key: str, point: GeoPoint) -> None:
        if self._conn is None:
            return
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO geocode_cache (query, lat, lon, label, province,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    key,
                    point.lat,
                    point.lon,
                    point.label,
                    point.province,
                    datetime.now(UTC).isoformat(),
                ),
            )


def _short_label(address: dict[str, object]) -> str | None:
    town = next(
        (address[k] for k in ("city", "town", "village", "municipality") if address.get(k)),
        None,
    )
    if town is None:
        return None
    postcode = address.get("postcode")
    return f"{postcode} {town}" if postcode else str(town)
