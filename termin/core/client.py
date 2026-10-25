"""Asynchroniczny klient API NFZ „Terminy leczenia” (ADR-0002).

- maks. 4 równoległe żądania (semafor),
- ponowienia z wykładniczym backoffem na 429, 5xx i błędy sieci (z `Retry-After`),
- `User-Agent` z linkiem do repo,
- cache odpowiedzi (SQLite albo pamięć),
- wstrzykiwany transport, więc testy nie wychodzą do sieci.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self
from urllib.parse import quote

import httpx

from termin import USER_AGENT
from termin.config import Settings
from termin.core.cache import Cache, MemoryCache
from termin.core.models import Location, Page, QueueEntry
from termin.core.parser import (
    parse_error,
    parse_many_places,
    parse_queue,
    parse_queues,
    parse_string_dictionary,
)
from termin.logs import get_logger
from termin.metrics import CACHE_LOOKUPS, NFZ_ERRORS, NFZ_LATENCY, NFZ_REQUESTS

log = get_logger(__name__)

PAGE_LIMIT = 25  # maksimum API NFZ
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
LIST_ENDPOINTS = frozenset({"/queues", "/benefits", "/localities"})
MAX_BACKOFF_S = 30.0


class NfzError(RuntimeError):
    """Ogólny błąd komunikacji z NFZ."""


class NfzUnavailableError(NfzError):
    """API NFZ nie odpowiada poprawnie mimo ponowień."""


class NfzBadRequestError(NfzError):
    """API NFZ odrzuciło parametry zapytania (4xx)."""


class NfzNotFoundError(NfzBadRequestError):
    """Zasób nie istnieje (404), np. nieaktualny identyfikator kolejki."""


@dataclass(frozen=True, slots=True)
class QueueQuery:
    """Parametry `/queues`. `benefit` działa pełnotekstowo (fragment nazwy)."""

    benefit: str
    province: str | None = None
    case: int = 1
    locality: str | None = None
    provider: str | None = None
    place: str | None = None
    street: str | None = None

    def params(self) -> dict[str, str]:
        out = {"benefit": self.benefit, "case": str(self.case)}
        for key in ("province", "locality", "provider", "place", "street"):
            value = getattr(self, key)
            if value:
                out[key] = str(value)
        return out


Sleep = Callable[[float], Awaitable[None]]


RATE_WINDOW_S = 10.0


class NfzClient:
    def __init__(
        self,
        settings: Settings,
        *,
        cache: Cache | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._cache: Cache = cache if cache is not None else MemoryCache()
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(settings.nfz_concurrency)
        self._slots: deque[float] = deque(maxlen=settings.nfz_requests_per_10s)
        self._clock = time.monotonic
        self._http = httpx.AsyncClient(
            base_url=settings.nfz_base_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=settings.nfz_timeout_s,
            transport=transport,
            follow_redirects=True,
        )
        self.requests_made = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ niski poziom

    def _cache_key(self, path: str, params: dict[str, str]) -> str:
        items = "&".join(f"{k}={params[k]}" for k in sorted(params))
        version = self._settings.nfz_api_version or "1.4"
        return f"nfz:{version}:{self._settings.nfz_base_url}{path}?{items}"

    async def get_json(self, path: str, params: dict[str, str], ttl_s: float) -> Any:
        """GET z cache i ponowieniami. `path` bez bazowego URL, np. '/queues'."""
        key = self._cache_key(path, params)
        cached = self._cache.get(key)
        if cached is not None:
            CACHE_LOOKUPS.labels("hit").inc()
            return cached
        CACHE_LOOKUPS.labels("miss").inc()

        try:
            payload = await self._fetch(path, params)
        except (NfzUnavailableError, NfzNotFoundError) as exc:
            payload = await self._fallback(path, params, exc)
        self._cache.set(key, payload, ttl_s)
        return payload

    async def _fallback(self, path: str, params: dict[str, str], exc: NfzError) -> Any:
        """API 1.4 nie działa (albo nie zna listy): próbujemy zapasowego API 1.3."""
        fallback = self._settings.nfz_fallback_url
        is_list = path in LIST_ENDPOINTS
        if not fallback or fallback.rstrip("/") == self._settings.nfz_base_url.rstrip("/"):
            raise exc
        if isinstance(exc, NfzNotFoundError) and not is_list:
            raise exc  # nieznane id kolejki: 1.3 ma inne identyfikatory, nie szukamy dalej
        log.warning("nfz.fallback", path=path, reason=type(exc).__name__)
        NFZ_REQUESTS.labels(path.split("/")[1], "fallback").inc()
        return await self._fetch(path, params, base=fallback.rstrip("/"), api_version="1.3")

    async def _fetch(
        self,
        path: str,
        params: dict[str, str],
        *,
        base: str | None = None,
        api_version: str | None = None,
    ) -> Any:
        endpoint = path.split("/")[1] if path.count("/") else path
        full = {**params, "format": "json"}
        version = api_version or self._settings.nfz_api_version
        if version:
            full["api-version"] = version
        url = f"{base}{path}" if base else path
        attempts = self._settings.nfz_max_retries
        last_error = "brak odpowiedzi"
        for attempt in range(attempts):
            delay: float | None = None
            try:
                async with self._semaphore:
                    await self._throttle()
                    started = time.perf_counter()
                    self.requests_made += 1
                    response = await self._http.get(url, params=full)
                    NFZ_LATENCY.labels(endpoint).observe(time.perf_counter() - started)
            except httpx.TransportError as exc:
                NFZ_REQUESTS.labels(endpoint, "network_error").inc()
                last_error = type(exc).__name__
            else:
                status = response.status_code
                if status < 400:
                    NFZ_REQUESTS.labels(endpoint, "ok").inc()
                    try:
                        return response.json()
                    except ValueError as exc:
                        NFZ_ERRORS.labels(endpoint, "invalid_json").inc()
                        raise NfzError("NFZ zwróciło odpowiedź, która nie jest JSON-em") from exc
                NFZ_REQUESTS.labels(endpoint, str(status)).inc()
                if status not in RETRY_STATUSES:
                    reason = _safe_error(response) or f"HTTP {status}"
                    if status == 404:
                        raise NfzNotFoundError(f"NFZ nie zna tego zasobu: {reason}")
                    NFZ_ERRORS.labels(endpoint, "bad_request").inc()
                    raise NfzBadRequestError(f"NFZ odrzuciło zapytanie: {reason}")
                last_error = f"HTTP {status}"
                delay = _retry_after(response)

            if attempt + 1 < attempts:
                if delay is None:
                    delay = min(MAX_BACKOFF_S, 0.5 * 2**attempt) + random.uniform(0, 0.25)  # noqa: S311
                log.info("nfz.retry", endpoint=endpoint, attempt=attempt + 1, reason=last_error)
                await self._sleep(min(delay, MAX_BACKOFF_S))

        NFZ_ERRORS.labels(endpoint, "unavailable").inc()
        log.warning("nfz.unavailable", endpoint=endpoint, reason=last_error)
        raise NfzUnavailableError(f"API NFZ jest niedostępne ({last_error}). Spróbuj za chwilę.")

    async def _throttle(self) -> None:
        """Najwyżej N zapytań w oknie 10 s (limit API 1.4). Kolejne czekają na swój slot."""
        now = self._clock()
        slot = now
        if len(self._slots) == self._slots.maxlen:
            slot = max(now, self._slots[0] + RATE_WINDOW_S)
        self._slots.append(slot)  # rezerwacja przed await: kolejność jak przy zgłoszeniu
        if slot > now:
            await self._sleep(slot - now)

    # ------------------------------------------------------------------ endpointy

    async def queues_page(self, query: QueueQuery, page: int = 1) -> Page:
        params = {**query.params(), "page": str(page), "limit": str(PAGE_LIMIT)}
        payload = await self.get_json("/queues", params, self._settings.cache_ttl_queues_s)
        return parse_queues(payload)

    async def queues(self, query: QueueQuery, max_pages: int | None = None) -> list[QueueEntry]:
        """Wszystkie strony wyników (do limitu). Strony 2+ pobierane równolegle."""
        cap = max_pages or self._settings.nfz_max_pages
        first = await self.queues_page(query, 1)
        entries = list(first.entries)
        if first.count is not None:
            total_pages = max(1, -(-first.count // PAGE_LIMIT))
        else:
            total_pages = 2 if first.has_next else 1
        if total_pages > cap:
            log.warning("nfz.pages_capped", pages=total_pages, cap=cap)
        last_page = min(total_pages, cap)

        if first.count is None and first.has_next:
            # Bez meta.count idziemy sekwencyjnie po links.next.
            page_no = 2
            while page_no <= cap:
                page = await self.queues_page(query, page_no)
                entries.extend(page.entries)
                if not page.has_next:
                    break
                page_no += 1
            return entries

        if last_page > 1:
            pages = await asyncio.gather(
                *(self.queues_page(query, n) for n in range(2, last_page + 1))
            )
            for page in pages:
                entries.extend(page.entries)
        return entries

    async def queue(self, queue_id: str) -> QueueEntry | None:
        try:
            payload = await self.get_json(
                f"/queues/{quote(queue_id, safe='')}", {}, self._settings.cache_ttl_queues_s
            )
        except NfzNotFoundError:
            return None
        return parse_queue(payload)

    async def many_places(self, queue_id: str) -> tuple[Location, ...]:
        try:
            payload = await self.get_json(
                f"/many-places/{quote(queue_id, safe='')}",
                {},
                self._settings.cache_ttl_dictionary_s,
            )
        except NfzNotFoundError:
            return ()
        return parse_many_places(payload)

    async def _dictionary(self, path: str, params: dict[str, str], max_pages: int) -> list[str]:
        names: list[str] = []
        for page_no in range(1, max_pages + 1):
            payload = await self.get_json(
                path,
                {**params, "page": str(page_no), "limit": str(PAGE_LIMIT)},
                self._settings.cache_ttl_dictionary_s,
            )
            page_names, has_next = parse_string_dictionary(payload)
            names.extend(page_names)
            if not has_next:
                break
        return list(dict.fromkeys(names))

    async def benefits(self, name: str, max_pages: int = 4) -> list[str]:
        """Nazwy świadczeń zawierające `name` (słownik NFZ; bez niego lista wbudowana)."""
        try:
            return await self._dictionary("/benefits", {"name": name}, max_pages)
        except NfzError:
            from termin.core.benefits import known_benefit_names
            from termin.core.text import fold

            needle = fold(name)
            return [n for n in known_benefit_names() if needle in fold(n)]

    async def localities(
        self, name: str, province: str | None = None, max_pages: int = 2
    ) -> list[str]:
        params = {"name": name}
        if province:
            params["province"] = province
        return await self._dictionary("/localities", params, max_pages)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value and value.strip().isdigit():
        return float(value.strip())
    return None


def _safe_error(response: httpx.Response) -> str | None:
    try:
        return parse_error(response.json())
    except ValueError:
        return None
