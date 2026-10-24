-- Cache odpowiedzi HTTP (NFZ) i geokodowania.
CREATE TABLE IF NOT EXISTS http_cache (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_http_cache_expires ON http_cache (expires_at);

CREATE TABLE IF NOT EXISTS geocode_cache (
    query       TEXT PRIMARY KEY,
    lat         REAL,
    lon         REAL,
    label       TEXT,
    province    TEXT,
    created_at  TEXT NOT NULL
);
