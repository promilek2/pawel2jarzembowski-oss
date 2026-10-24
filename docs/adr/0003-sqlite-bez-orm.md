# ADR-0003: SQLite przez sqlite3 z prostymi migracjami (bez ORM)

- Status: przyjęty
- Data: 2026-10-24

## Kontekst

Potrzebujemy trwałego magazynu na snapshoty wyników, cache, geokodowanie i
subskrypcje. Wdrożenie ma być trywialne: jeden plik, jeden wolumen Dockera.

## Decyzja

- Standardowy `sqlite3` + numerowane migracje SQL w `termin/store/migrations/`
  (tabela `schema_migrations`). Bez SQLAlchemy.
- Tryb WAL, `foreign_keys=ON`, jedna baza (`~/.local/share/termin/termin.db`,
  w Dockerze `/data/termin.db`).
- Operacje są krótkie i synchroniczne. W kontekście asynchronicznym wywołujemy
  je bezpośrednio, bo trwają mikrosekundy–milisekundy przy tej skali.

## Konsekwencje

- Zero dodatkowych zależności, łatwy backup (`sqlite3 termin.db .backup`).
- Zapytania analityczne (percentyle) liczymy w Pythonie (`statistics`), bo
  SQLite nie ma funkcji percentyla.
- Przy dużym ruchu web + scheduler możliwe blokady zapisu. WAL i
  `busy_timeout` wystarczą przy oczekiwanej skali. Ewentualna migracja na
  Postgres wymagałaby warstwy repozytoriów, która już jest wydzielona w `store/`.
