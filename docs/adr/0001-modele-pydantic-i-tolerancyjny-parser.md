# ADR-0001: Modele Pydantic v2 i tolerancyjny parser

- Status: przyjęty
- Data: 2026-10-24

## Kontekst

API NFZ zwraca JSON w stylu JSON:API z polami w kebab-case. Wiele pól bywa
`null` (`dates`, `statistics.computed-data`, `age-range`, współrzędne), a
współrzędne bywają równe `0`. Flagi mają postać `"Y"`/`"N"`. Kod musi to
przeżyć, a jednocześnie chcemy mieć ścisłe typy (`mypy --strict`).

## Decyzja

- Modele domenowe w `termin/core/models.py` na Pydantic v2 z `alias` dla pól
  kebab-case i `populate_by_name=True`.
- Normalizacja w walidatorach `mode="before"`: `"Y"/"N"` → `bool | None`,
  `0`/`""`/poza zakresem Polski → brak współrzędnych, puste stringi → `None`,
  daty w kilku formatach → `date | None`.
- Parser (`termin/core/parser.py`) **nie zakłada kształtu** tam, gdzie schemat
  jest niepewny (`/many-places/{id}`): rekurencyjnie szuka obiektów z polami
  adresowymi i współrzędnymi.
- Błędny pojedynczy rekord jest pomijany i logowany (bez danych osobowych),
  a nie wywraca całego wyszukiwania.

## Konsekwencje

- Reszta kodu pracuje na typach (`date`, `float | None`), a nie na słownikach.
- Nowe pola API nie psują parsowania (`extra="ignore"`).
- Koszt: walidatory trzeba testować na fixtures z przypadkami brzegowymi.
