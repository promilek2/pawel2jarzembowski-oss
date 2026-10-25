-- Funkcje dla zalogowanych (ADR-0012): zgłoszenia „Sprawdziłem telefonicznie”, „Moje sprawy”,
-- prywatne notatki przy placówkach, powiadomienia Web Push.

-- Wyraźna zgoda na przechowywanie informacji o własnych sprawach zdrowotnych (art. 9 RODO).
ALTER TABLE users ADD COLUMN health_consent_at TEXT;

-- Wynik telefonu do poradni. Bez wolnego tekstu: nie ma czego moderować ani kogo zniesławić.
CREATE TABLE IF NOT EXISTS phone_reports (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    queue_id      TEXT NOT NULL,
    report_day    TEXT NOT NULL,          -- RRRR-MM-DD; jedno zgłoszenie na kolejkę dziennie
    created_at    TEXT NOT NULL,
    reached       INTEGER NOT NULL,       -- 1 = dodzwonił(a) się
    outcome       TEXT,                   -- 'same' | 'other' | 'none' (gdy reached = 1)
    nfz_date      TEXT,                   -- termin z danych NFZ w chwili zgłoszenia
    offered_date  TEXT,                   -- termin zaproponowany przez rejestrację
    UNIQUE (user_id, queue_id, report_day)
);
CREATE INDEX IF NOT EXISTS ix_phone_reports_queue ON phone_reports (queue_id, report_day);

CREATE TABLE IF NOT EXISTS cases (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    label         TEXT NOT NULL,
    queue_id      TEXT,
    provider      TEXT,
    address       TEXT,
    phone         TEXT,
    status        TEXT NOT NULL DEFAULT 'to_call',
    visit_date    TEXT,
    note          TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    reminded_for  TEXT                    -- data wizyty, o której już przypomnieliśmy
);
CREATE INDEX IF NOT EXISTS ix_cases_user ON cases (user_id);
CREATE INDEX IF NOT EXISTS ix_cases_visit ON cases (visit_date);

CREATE TABLE IF NOT EXISTS facility_notes (
    user_id     INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    queue_id    TEXT NOT NULL,
    note        TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, queue_id)
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    endpoint    TEXT NOT NULL UNIQUE,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    device      TEXT,
    created_at  TEXT NOT NULL,
    last_ok_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_push_user ON push_subscriptions (user_id);

-- Sekrety generowane przez aplikację (klucz VAPID do Web Push), gdy nie podano ich w konfiguracji.
CREATE TABLE IF NOT EXISTS app_secrets (
    name        TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
