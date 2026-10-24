-- Subskrypcje powiadomień. Bez kont: identyfikacją jest losowy token.
-- Współrzędne zaokrąglamy do 2 miejsc po przecinku (~1 km), zanim trafią do bazy.
CREATE TABLE IF NOT EXISTS subscriptions (
    token               TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,
    label               TEXT NOT NULL,
    benefit_query       TEXT NOT NULL,
    variant             TEXT NOT NULL DEFAULT 'wszystkie',
    exclude             TEXT NOT NULL DEFAULT '[]',
    case_no             INTEGER NOT NULL DEFAULT 1,
    province            TEXT,
    lat                 REAL,
    lon                 REAL,
    radius_km           REAL,
    area_label          TEXT,
    before_date         TEXT,
    channel             TEXT NOT NULL,
    target              TEXT NOT NULL,
    last_notified_date  TEXT,
    last_notified_queue TEXT,
    last_notified_at    TEXT,
    last_checked_at     TEXT,
    active              INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_subscriptions_target ON subscriptions (channel, target);
