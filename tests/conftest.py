from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from termin.config import Settings
from termin.core.cache import MemoryCache
from termin.core.client import NfzClient
from termin.runtime import Runtime, Transports
from termin.store.db import connect
from tests.fake_nfz import FakeNfz

TODAY = dt.date(2026, 10, 24)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Żaden test nie czyta prawdziwego configu ani nie pisze do $HOME."""
    for key in list(__import__("os").environ):
        if key.startswith("TERMIN_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TERMIN_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    from termin.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", geocoder="offline")


@pytest.fixture
def fake_nfz() -> FakeNfz:
    return FakeNfz.with_defaults()


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def client(settings: Settings, fake_nfz: FakeNfz) -> AsyncIterator[NfzClient]:
    async with NfzClient(
        settings, cache=MemoryCache(), transport=fake_nfz.transport(), sleep=_no_sleep
    ) as c:
        yield c


@pytest.fixture
async def runtime(settings: Settings, fake_nfz: FakeNfz) -> AsyncIterator[Runtime]:
    rt = Runtime(
        settings,
        transports=Transports(nfz=fake_nfz.transport()),
        conn=connect(":memory:"),
        today=lambda: TODAY,
    )
    yield rt
    await rt.aclose()
    rt.conn.close()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Testy e2e/live tylko na żądanie (-m e2e / -m live), nawet przy wskazaniu pliku."""
    selected = config.getoption("-m") or ""
    for item in items:
        for marker in ("e2e", "live"):
            if item.get_closest_marker(marker) and marker not in selected:
                item.add_marker(pytest.mark.skip(reason=f"uruchom z -m {marker}"))
