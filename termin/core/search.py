"""Orkiestracja wyszukiwania: zapytanie → świadczenie → lokalizacja → NFZ → ranking."""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field

from termin.config import Settings
from termin.core.benefits import BenefitCatalog, Resolution, Variant
from termin.core.client import NfzClient, NfzError, QueueQuery
from termin.core.geo import Geocoder
from termin.core.models import Case, GeoPoint, Location, QueueEntry, RankedResult, SortMode
from termin.core.parser import ApiFormatError
from termin.core.provinces import PROVINCES, province_from_teryt, provinces_within
from termin.core.ranking import (
    Candidate,
    RankingOptions,
    nearest_location,
    province_medians,
    rank,
)
from termin.core.routing import Router
from termin.logs import get_logger

log = get_logger(__name__)

MAX_MANY_PLACES_LOOKUPS = 30


class SearchError(ValueError):
    """Błąd po stronie zapytania użytkownika (komunikat do pokazania)."""


@dataclass(frozen=True, slots=True)
class SearchRequest:
    text: str
    where: str | None = None
    point: GeoPoint | None = None
    radius_km: float | None = None
    province: str | None = None
    case: Case = Case.STABLE
    variant: Variant | None = None
    mode: SortMode = SortMode.BALANCED
    km_per_day: float | None = None
    limit: int = 20
    with_travel_time: bool = False
    benefit_query: str | None = None
    """Jawny fragment nazwy świadczenia (pomija słownik), np. z wyboru w UI."""


@dataclass(slots=True)
class SearchResponse:
    request: SearchRequest
    resolution: Resolution
    point: GeoPoint | None = None
    provinces: list[str] = field(default_factory=list)
    results: list[RankedResult] = field(default_factory=list)
    total_found: int = 0
    """Ile pozycji pasowało (przed ucięciem do `limit`)."""
    without_location: int = 0
    """Ile pozycji pominięto, bo nie mają współrzędnych (przy wyszukiwaniu w promieniu)."""
    benefit_names: list[str] = field(default_factory=list)
    medians: dict[str, float] = field(default_factory=dict)
    all_entries: list[tuple[str | None, QueueEntry]] = field(default_factory=list)
    """Wszystkie pobrane pozycje (do snapshotów i statystyk)."""
    today: dt.date = field(default_factory=dt.date.today)

    @property
    def needs_input(self) -> bool:
        return self.resolution.needs_input


class SearchService:
    def __init__(
        self,
        settings: Settings,
        client: NfzClient,
        geocoder: Geocoder,
        catalog: BenefitCatalog | None = None,
        router: Router | None = None,
        today: Callable[[], dt.date] = dt.date.today,
    ) -> None:
        self.settings = settings
        self.client = client
        self.geocoder = geocoder
        self.catalog = catalog or BenefitCatalog.load()
        self.router = router
        self._today = today

    def resolve(self, req: SearchRequest) -> Resolution:
        if req.benefit_query:
            base = Resolution("ok", req.text or req.benefit_query, req.benefit_query.upper())
            entry = next(
                (e for e in self.catalog.entries if e.query == req.benefit_query.upper()), None
            )
            if entry is not None:
                res = Resolution("ok", base.text, entry.query, entry)
                return res.with_variant(req.variant) if req.variant else res
            return base
        return self.catalog.resolve(req.text, req.variant)

    async def locate(self, req: SearchRequest) -> GeoPoint | None:
        if req.point is not None:
            return req.point
        if req.where:
            point = await self.geocoder.geocode(req.where)
            if point is None:
                raise SearchError(
                    f"Nie udało się znaleźć miejsca „{req.where}”. Podaj miasto, kod pocztowy "
                    "albo pełny adres."
                )
            return point
        return None

    def choose_provinces(self, req: SearchRequest, point: GeoPoint | None) -> list[str]:
        if req.province:
            if req.province not in PROVINCES:
                raise SearchError(f"Nieznany kod województwa: {req.province}")
            return [req.province]
        if point is None:
            raise SearchError("Podaj, gdzie jesteś (miasto, kod pocztowy) albo województwo.")
        radius = req.radius_km or self.settings.default_radius_km
        codes = set(provinces_within(point.lat, point.lon, radius))
        if point.province:
            codes.add(point.province)
        return sorted(codes)

    async def search(self, req: SearchRequest) -> SearchResponse:
        today = self._today()
        resolution = self.resolve(req)
        response = SearchResponse(request=req, resolution=resolution, today=today)
        if resolution.needs_input:
            return response

        point = await self.locate(req)
        provinces = self.choose_provinces(req, point)
        response.point, response.provinces = point, provinces

        pages = await asyncio.gather(
            *(
                self.client.queues(
                    QueueQuery(benefit=resolution.query, province=code, case=int(req.case))
                )
                for code in provinces
            )
        )
        seen: set[str] = set()
        fetched: list[tuple[str | None, QueueEntry]] = []
        for code, entries in zip(provinces, pages, strict=True):
            for entry in entries:
                if entry.id in seen or not resolution.matches(entry):
                    continue
                seen.add(entry.id)
                fetched.append((province_from_teryt(entry.teryt_place) or code, entry))

        fetched = await self._expand_many_places(fetched, point)
        response.all_entries = fetched
        response.benefit_names = sorted({e.benefit for _, e in fetched})
        response.medians = province_medians(fetched, today)

        radius = req.radius_km or (self.settings.default_radius_km if point else None)
        if req.province and req.radius_km is None:
            radius = None  # jawne województwo bez promienia: całe województwo
        candidates: list[Candidate] = []
        for province, entry in fetched:
            location, distance = nearest_location(entry, point)
            if point is not None and radius is not None:
                if distance is None:
                    response.without_location += 1
                    continue
                if distance > radius:
                    continue
            candidates.append(
                Candidate(
                    entry=entry,
                    location=location,
                    province=province,
                    distance_km=distance,
                    nearest_of_many=bool(entry.extra_locations)
                    and location != entry.all_locations[0],
                )
            )

        if req.with_travel_time and point is not None and self.router is not None:
            candidates = await self._with_travel_time(candidates, point)

        opts = RankingOptions(
            today=today,
            mode=req.mode,
            km_per_day=req.km_per_day or self.settings.km_per_day,
            minutes_per_day=self.settings.minutes_per_day,
            stale_after_days=self.settings.stale_after_days,
        )
        ranked = rank(candidates, opts, response.medians)
        response.total_found = len(ranked)
        response.results = ranked[: req.limit]
        log.info(
            "search.done",
            benefit=resolution.query,
            provinces=len(provinces),
            fetched=len(fetched),
            results=len(ranked),
        )
        return response

    async def _expand_many_places(
        self, fetched: list[tuple[str | None, QueueEntry]], point: GeoPoint | None
    ) -> list[tuple[str | None, QueueEntry]]:
        if point is None:
            return fetched
        targets = [e.id for _, e in fetched if e.many_places][:MAX_MANY_PLACES_LOOKUPS]
        if not targets:
            return fetched

        async def lookup(queue_id: str) -> tuple[str, tuple[Location, ...]]:
            try:
                return queue_id, await self.client.many_places(queue_id)
            except (NfzError, ApiFormatError) as exc:  # brak filii nie psuje wyszukiwania
                log.info("search.many_places_failed", reason=type(exc).__name__)
                return queue_id, ()

        found = dict(await asyncio.gather(*(lookup(t) for t in targets)))
        out: list[tuple[str | None, QueueEntry]] = []
        for province, entry in fetched:
            extra = found.get(entry.id)
            if extra:
                entry = entry.model_copy(update={"extra_locations": extra})
            out.append((province, entry))
        return out

    async def _with_travel_time(
        self, candidates: list[Candidate], point: GeoPoint
    ) -> list[Candidate]:
        assert self.router is not None
        with_coords = [c for c in candidates if c.location.has_coords]
        dests = [
            (c.location.latitude, c.location.longitude)
            for c in with_coords
            if c.location.latitude is not None and c.location.longitude is not None
        ]
        minutes = await self.router.durations_min((point.lat, point.lon), dests)
        by_id = {c.entry.id: m for c, m in zip(with_coords, minutes, strict=True)}
        return [
            Candidate(
                entry=c.entry,
                location=c.location,
                province=c.province,
                distance_km=c.distance_km,
                travel_min=by_id.get(c.entry.id),
                nearest_of_many=c.nearest_of_many,
            )
            for c in candidates
        ]
