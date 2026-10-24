# ADR-0007: Fixtures zrekonstruowane ze schematu (brak dostępu do API podczas budowy)

- Status: przyjęty (tymczasowo, do zastąpienia prawdziwymi odpowiedziami)
- Data: 2026-10-24

## Kontekst

Plan zakładał pobranie prawdziwych odpowiedzi `/queues`, `/queues/{id}`,
`/many-places/{id}`, `/benefits`, `/localities` jako fixtures. Środowisko, w
którym budowano v1.0, nie miało dostępu sieciowego do `api.nfz.gov.pl`
(polityka proxy zwracała 403).

## Decyzja

- Fixtures w `tests/fixtures/` są **ręcznie zrekonstruowane** na podstawie
  znanego schematu API 1.3 (Swagger, otwarte klienty API NFZ). Zawierają
  celowo wszystkie znane przypadki brzegowe: `dates: null`, `computed-data: null`,
  `age-range: null`, współrzędne `0` i `null`, terminy w przeszłości, brak telefonu.
  Każdy plik ma to oznaczone w `tests/fixtures/README.md`.
- `scripts/capture_fixtures.py` pobiera prawdziwe odpowiedzi i zapisuje je obok
  (`tests/fixtures/live/`). Testy kontraktowe przechodzą przez parser także te
  pliki, jeśli istnieją.
- Testy `@pytest.mark.live` (domyślnie pomijane) sprawdzają kontrakt na żywym API.
- Kształt `/many-places/{id}` jest najmniej pewny, dlatego parser szuka w nim
  obiektów z polami adresowymi rekurencyjnie (ADR-0001).

## Konsekwencje

- Pierwsze uruchomienie `python scripts/capture_fixtures.py && pytest -m live`
  z sieci z dostępem do NFZ jest **obowiązkowym krokiem przed wydaniem 1.0 na PyPI**.
- Jeśli prawdziwy schemat się różni, poprawki ograniczą się do `parser.py`.
