-- Konta (opcjonalne). Hasła: scrypt. Tokeny sesji i e-mail: tylko skrót SHA-256.
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY,
    email               TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    email_verified_at   TEXT,
    terms_accepted_at   TEXT NOT NULL,
    terms_version       TEXT NOT NULL,
    failed_logins       INTEGER NOT NULL DEFAULT 0,
    locked_until        TEXT,
    last_login_at       TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash      TEXT PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    csrf_token      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    last_seen_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions (user_id);
CREATE INDEX IF NOT EXISTS ix_sessions_expires ON sessions (expires_at);

CREATE TABLE IF NOT EXISTS email_tokens (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    purpose     TEXT NOT NULL,          -- 'verify' | 'reset'
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_at     TEXT
);

-- Obserwacje mogą należeć do konta; usunięcie konta usuwa je (ON DELETE CASCADE).
-- consent_at: wyraźna zgoda na przetwarzanie informacji o świadczeniu (art. 9 ust. 2 lit. a RODO).
ALTER TABLE subscriptions ADD COLUMN user_id INTEGER REFERENCES users (id) ON DELETE CASCADE;
ALTER TABLE subscriptions ADD COLUMN consent_at TEXT;
CREATE INDEX IF NOT EXISTS ix_subscriptions_user ON subscriptions (user_id);
