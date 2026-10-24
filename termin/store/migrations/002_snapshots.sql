-- Snapshoty wyników NFZ: tylko dane publiczne (placówki, terminy), bez danych użytkownika.
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY,
    taken_at    TEXT NOT NULL,          -- ISO 8601 UTC
    taken_on    TEXT NOT NULL,          -- data (YYYY-MM-DD), jeden snapshot na klucz dziennie
    query       TEXT NOT NULL,          -- fragment nazwy świadczenia wysłany do NFZ
    province    TEXT NOT NULL,          -- kod NFZ 01–16
    case_no     INTEGER NOT NULL,       -- 1 stabilny, 2 pilny
    source      TEXT NOT NULL,          -- 'search' | 'scheduler'
    UNIQUE (query, province, case_no, taken_on)
);

CREATE TABLE IF NOT EXISTS snapshot_rows (
    snapshot_id     INTEGER NOT NULL REFERENCES snapshots (id) ON DELETE CASCADE,
    queue_id        TEXT NOT NULL,
    benefit         TEXT NOT NULL,
    provider        TEXT,
    place           TEXT,
    address         TEXT,
    locality        TEXT,
    phone           TEXT,
    province        TEXT,
    teryt           TEXT,
    first_date      TEXT,
    as_at           TEXT,
    awaiting        INTEGER,
    average_period  INTEGER,
    latitude        REAL,
    longitude       REAL,
    for_children    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (snapshot_id, queue_id)
);

CREATE INDEX IF NOT EXISTS ix_snapshot_rows_queue ON snapshot_rows (queue_id);
CREATE INDEX IF NOT EXISTS ix_snapshots_taken ON snapshots (taken_on);
