"""Cache z TTL: w pamięci (testy, krótkie sesje) albo w SQLite (domyślnie)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol


class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl_s: float) -> None: ...


class NullCache:
    """Cache, który niczego nie pamięta (np. `--bez-cache`)."""

    def get(self, key: str) -> Any | None:
        return None

    def set(self, key: str, value: Any, ttl_s: float) -> None:
        return None


class MemoryCache:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._clock = clock
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None or item[0] < self._clock():
                self._data.pop(key, None)
                self.misses += 1
                return None
            self.hits += 1
            return item[1]

    def set(self, key: str, value: Any, ttl_s: float) -> None:
        with self._lock:
            self._data[key] = (self._clock() + ttl_s, value)


class SqliteCache:
    """Cache w tabeli `http_cache`. Wartości trzymamy jako JSON."""

    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], float] = time.time) -> None:
        self._conn = conn
        self._clock = clock
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value, expires_at FROM http_cache WHERE key = ?", (key,)
            ).fetchone()
            if row is None or row[1] < self._clock():
                self.misses += 1
                return None
            self.hits += 1
            return json.loads(row[0])

    def set(self, key: str, value: Any, ttl_s: float) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO http_cache (key, value, expires_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " expires_at = excluded.expires_at",
                (key, json.dumps(value, ensure_ascii=False), self._clock() + ttl_s),
            )

    def purge_expired(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM http_cache WHERE expires_at < ?", (self._clock(),)
            )
            return cur.rowcount
