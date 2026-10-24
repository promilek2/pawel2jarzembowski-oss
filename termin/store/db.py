"""Połączenie z SQLite i migracje (numerowane pliki SQL w `migrations/`)."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

from termin.logs import get_logger

log = get_logger(__name__)


def _migrations() -> list[tuple[int, str, str]]:
    files = resources.files("termin.store").joinpath("migrations")
    out: list[tuple[int, str, str]] = []
    for item in files.iterdir():
        name = item.name
        if name.endswith(".sql") and name[:3].isdigit():
            out.append((int(name[:3]), name, item.read_text(encoding="utf-8")))
    return sorted(out)


def migrate(conn: sqlite3.Connection) -> int:
    """Stosuje brakujące migracje. Zwraca numer najnowszej wersji schematu."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
        " applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    version = max(applied, default=0)
    for number, name, sql in _migrations():
        if number in applied:
            continue
        with conn:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)", (number, name)
            )
        log.debug("db.migrated", version=number, name=name)
        version = number
    return version


def connect(path: Path | str) -> sqlite3.Connection:
    """Otwiera bazę (tworzy katalog), włącza WAL i klucze obce, stosuje migracje."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn
