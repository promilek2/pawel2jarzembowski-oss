from __future__ import annotations

from pathlib import Path

from termin.core.cache import MemoryCache, NullCache, SqliteCache
from termin.store.db import connect, migrate


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_memory_cache_ttl() -> None:
    clock = Clock()
    cache = MemoryCache(clock)
    cache.set("a", {"x": 1}, 10)
    assert cache.get("a") == {"x": 1}
    clock.now += 11
    assert cache.get("a") is None
    assert cache.hits == 1 and cache.misses == 1


def test_null_cache() -> None:
    cache = NullCache()
    cache.set("a", 1, 10)
    assert cache.get("a") is None


def test_sqlite_cache_persists_and_expires(tmp_path: Path) -> None:
    clock = Clock()
    db = tmp_path / "t.db"
    first, second = connect(db), connect(db)
    cache = SqliteCache(first, clock)
    cache.set("k", {"data": ["Ł"]}, 60)
    cache.set("k", {"data": ["Ż"]}, 60)  # nadpisanie
    reopened = SqliteCache(second, clock)
    assert reopened.get("k") == {"data": ["Ż"]}
    clock.now += 61
    assert reopened.get("k") is None
    assert reopened.purge_expired() == 1
    first.close()
    second.close()


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    conn = connect(tmp_path / "m.db")
    version = migrate(conn)
    assert version >= 1
    assert migrate(conn) == version
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"http_cache", "geocode_cache", "schema_migrations"} <= tables
    conn.close()


def test_memory_database() -> None:
    conn = connect(":memory:")
    assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] >= 1
    conn.close()
