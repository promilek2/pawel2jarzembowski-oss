# Fixtures API NFZ

> **Uwaga:** pliki w tym katalogu są **zrekonstruowane** ze schematu API NFZ 1.3,
> a nie przechwycone z prawdziwego API. Środowisko budowy v1.0 nie miało dostępu
> do `api.nfz.gov.pl` (ADR-0007). Nazwy placówek i numery telefonów są fikcyjne.

| Plik | Endpoint | Co testuje |
|------|----------|------------|
| `queues_okulistyczna_11_p1.json`, `_p2.json` | `/queues` | paginacja (30 pozycji, 2 strony), `dates: null`, brak terminu, współrzędne `0`/`null`/z przecinkiem/poza Polską, brak telefonu, termin w przeszłości, stare dane, `statistics: null`, tylko `computed-data`, wariant dla dzieci, `age-range` jako string i obiekt, `many-places: Y` |
| `queue_single.json` | `/queues/{id}` | pojedynczy rekord (`data` jako obiekt) |
| `many_places.json` | `/many-places/{id}` | lokalizacje filii, współrzędne `0` (kształt odpowiedzi niepewny) |
| `benefits_okul.json` | `/benefits` | słownik świadczeń jako lista stringów |
| `localities_gdy.json` | `/localities` | słownik miejscowości |
| `queues_empty.json` | `/queues` | brak wyników |
| `error_400.json` | dowolny | format błędu `errors[].error-reason` |

Generator: `python tests/fixtures/_generate.py`.

Prawdziwe odpowiedzi: `python scripts/capture_fixtures.py` zapisuje je do `live/`,
a `tests/test_parser.py::test_live_fixtures_parse_if_present` przepuszcza je przez parser.
