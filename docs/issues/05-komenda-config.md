# Komenda `termin config`

Etykiety: `good first issue`, `cli`

## Kontekst
Konfiguracja pochodzi z kilku miejsc (zmienne `TERMIN_*`, `~/.config/termin/config.toml`,
wartości domyślne). Przy zgłoszeniach błędów trudno ustalić, jakie ustawienia faktycznie działają.

## Do zrobienia
- Dodaj komendę `termin config` w `termin/cli/commands.py`, która wypisze: ścieżkę pliku
  konfiguracyjnego (`config_file()`), czy istnieje, ścieżkę bazy (`Settings.db_path`) oraz
  wszystkie ustawienia.
- **Sekrety maskuj** (`telegram_token`, `smtp_password` → `***`).
- Opcja `--json`.

## Gotowe, gdy
- test w `tests/test_cli.py` sprawdza, że token nie pojawia się w wyjściu,
- `pytest` przechodzi.
