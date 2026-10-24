"""Czas dojazdu autem przez OSRM (opcjonalnie, ADR-0006).

Używamy usługi `table`: jedno żądanie dla punktu startowego i wielu celów. Publiczny
serwer demo (router.project-osrm.org) jest tylko do małego ruchu, więc domyślnie
OSRM jest wyłączony (`TERMIN_OSRM_URL`). Błąd OSRM nie psuje wyszukiwania: wtedy
zwracamy brak czasów i zostaje odległość po prostej.
"""

from __future__ import annotations

import httpx

from termin import USER_AGENT
from termin.logs import get_logger

log = get_logger(__name__)

MAX_DESTINATIONS = 90


class Router:
    def __init__(self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"User-Agent": USER_AGENT},
            timeout=15.0,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def durations_min(
        self, origin: tuple[float, float], destinations: list[tuple[float, float]]
    ) -> list[float | None]:
        """Czasy dojazdu w minutach (None, gdy brak trasy albo błąd)."""
        out: list[float | None] = []
        for start in range(0, len(destinations), MAX_DESTINATIONS):
            chunk = destinations[start : start + MAX_DESTINATIONS]
            out.extend(await self._chunk(origin, chunk))
        return out

    async def _chunk(
        self, origin: tuple[float, float], dests: list[tuple[float, float]]
    ) -> list[float | None]:
        if not dests:
            return []
        coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in [origin, *dests])
        try:
            response = await self._http.get(
                f"/table/v1/driving/{coords}",
                params={"sources": "0", "annotations": "duration"},
            )
            response.raise_for_status()
            data = response.json()
            row = data["durations"][0][1:]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            log.info("osrm.unavailable", reason=type(exc).__name__)
            return [None] * len(dests)
        return [round(v / 60, 1) if isinstance(v, int | float) else None for v in row]
