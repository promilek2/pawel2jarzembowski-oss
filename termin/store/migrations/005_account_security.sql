-- Weryfikacja dwuetapowa (TOTP), kody odzyskiwania, opis urządzenia przy sesji.
ALTER TABLE users ADD COLUMN totp_secret TEXT;          -- ustawiany przy konfiguracji
ALTER TABLE users ADD COLUMN totp_enabled_at TEXT;      -- NULL = 2FA wyłączone
ALTER TABLE users ADD COLUMN totp_last_step INTEGER;    -- ochrona przed ponownym użyciem kodu

-- Zgrubny opis urządzenia („Chrome · Android”), bez pełnego User-Agent i bez IP.
ALTER TABLE sessions ADD COLUMN device TEXT;
-- 1 = „nie wylogowuj mnie” (ciasteczko trwałe), 0 = sesja do zamknięcia przeglądarki.
ALTER TABLE sessions ADD COLUMN persistent INTEGER NOT NULL DEFAULT 1;

-- Jednorazowe kody odzyskiwania (tylko skrót SHA-256; kod ma 80 bitów losowości).
CREATE TABLE IF NOT EXISTS recovery_codes (
    code_hash   TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    used_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_recovery_codes_user ON recovery_codes (user_id);

-- Drugi krok logowania: po poprawnym haśle, przed utworzeniem sesji.
CREATE TABLE IF NOT EXISTS login_challenges (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    persistent  INTEGER NOT NULL DEFAULT 1,
    expires_at  TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0
);
