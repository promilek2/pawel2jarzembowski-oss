"""Złożenie zależności (klient NFZ, geokoder, wyszukiwarka, baza) dla CLI, web i watchera."""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Self

import httpx

from termin.config import Settings
from termin.core.benefits import BenefitCatalog
from termin.core.cache import SqliteCache
from termin.core.client import NfzClient
from termin.core.geo import Geocoder
from termin.core.routing import Router
from termin.core.search import SearchService
from termin.store.db import connect


@dataclass
class Transports:
    """Wstrzykiwane transporty HTTP (testy, tryb demo). None = prawdziwa sieć."""

    nfz: httpx.AsyncBaseTransport | None = None
    geo: httpx.AsyncBaseTransport | None = None
    osrm: httpx.AsyncBaseTransport | None = None
    notify: httpx.AsyncBaseTransport | None = None


class Runtime:
    def __init__(
        self,
        settings: Settings,
        *,
        transports: Transports | None = None,
        conn: sqlite3.Connection | None = None,
        today: Callable[[], dt.date] = dt.date.today,
    ) -> None:
        self.settings = settings
        self.transports = transports or Transports()
        self._owns_conn = conn is None
        self.conn = conn if conn is not None else connect(settings.db_path)
        self.cache = SqliteCache(self.conn)
        self.catalog = BenefitCatalog.load()
        self.client = NfzClient(settings, cache=self.cache, transport=self.transports.nfz)
        self.geocoder = Geocoder(settings, conn=self.conn, transport=self.transports.geo)
        self.router = (
            Router(settings.osrm_url, transport=self.transports.osrm) if settings.osrm_url else None
        )
        self.today = today
        self.search = SearchService(
            settings, self.client, self.geocoder, self.catalog, self.router, today=today
        )

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
        await self.client.aclose()
        await self.geocoder.aclose()
        if self.router is not None:
            await self.router.aclose()
        if self._owns_conn:
            self.conn.close()
