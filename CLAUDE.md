# CLAUDE.md: kontekst dla kolejnych sesji

**Termin**: open-source'owa aplikacja (Python 3.11+) do szukania najszybszego terminu u
specjalisty na NFZ. Dane pochodzą z publicznego API NFZ „Terminy leczenia”. Projekt nie
jest oficjalną aplikacją NFZ: bez logo NFZ, bez porad medycznych, bez trackingu.

## Komendy

```bash
uv venv && uv pip install -e ".[dev]"     # środowisko
pytest                                     # testy (bez sieci; live i e2e pomijane)
pytest -m live                             # kontrakt na prawdziwym API NFZ
pytest -m e2e                              # Playwright + axe (wymaga .[e2e])
ruff check . && ruff format --check .      # lint i format
mypy --strict termin                       # typy
python tests/fixtures/_generate.py         # regeneracja syntetycznych fixtures
python scripts/capture_fixtures.py         # prawdziwe odpowiedzi → tests/fixtures/live/
termin --help                              # CLI
```

Każda zmiana kończy się zielonymi: `pytest`, `ruff check`, `ruff format --check`,
`mypy --strict termin`. Commity w stylu Conventional Commits.

## Architektura

- `termin/core/`: logika domenowa (bez UI). `client.py` (NFZ, async httpx, semafor 4,
  retry, cache), `parser.py` + `models.py` (Pydantic v2, tolerancyjne), `provinces.py`,
  `text.py` (fold bez polskich znaków, odmiana), `geo_math.py` (haversine).
- `termin/store/`: SQLite przez `sqlite3`, migracje w `store/migrations/NNN_*.sql`.
- `termin/core/benefits.py`: synonimy (`termin/data/synonyms.toml`) + rapidfuzz, warianty
  dzieci/dorośli. `geo.py`: Nominatim + offline (`termin/data/places.csv`). `routing.py`: OSRM.
  `ranking.py`: tryby, ostrzeżenia (`Alert`), wyjaśnienia (`Reason`). `search.py`:
  `SearchService` (orkiestracja). `i18n.py`: teksty backendu z `termin/locales/pl.toml`.
- `termin/runtime.py`: `Runtime` składa zależności (klient, geokoder, wyszukiwarka, baza).
  `Transports` pozwala wstrzyknąć `httpx.MockTransport` w testach.
- `termin/cli/commands.py`: Typer (`make_runtime` to punkt wstrzyknięcia dla testów).
  `termin/web/`: FastAPI + statyczny frontend. `termin/notify/`: kanały powiadomień.
- `termin/config.py`: `Settings` (env `TERMIN_*`, plik `~/.config/termin/config.toml`,
  nadpisywany przez `TERMIN_CONFIG`).
- `termin/logs.py`: structlog z procesorem `redact_pii`. **Nigdy nie loguj lokalizacji
  użytkownika, kontaktów do powiadomień ani tokenów.** Dodając nowe pole wrażliwe,
  dopisz klucz do `SENSITIVE_KEYS`.
- Decyzje: `docs/adr/`. Plan i status faz: `docs/PLAN.md`.

## Konwencje

- Komunikaty dla użytkownika po polsku. Identyfikatory w kodzie po angielsku.
- Typy wszędzie (`mypy --strict`). Modele danych: Pydantic v2 (frozen).
- Testy nigdy nie wychodzą do sieci: `tests/fake_nfz.py` (`httpx.MockTransport`)
  udaje NFZ z filtrowaniem i paginacją. Do kodu, który robi HTTP, wstrzykuj `transport=`.
- Testy na żywym API: `@pytest.mark.live` (plik `tests/test_live.py`).
- Czas „dzisiaj” wstrzykujemy (parametr `today`), żeby testy były deterministyczne
  (fixtures zakładają dzisiaj = 2026-10-24).

## Pułapki API NFZ (zweryfikuj na żywym API, patrz ADR-0007)

- Bazowy URL `https://api.nfz.gov.pl/app-itl-api`, zawsze `format=json&api-version=1.3`.
- `limit` maks. 25. Paginacja: `meta.count` + `links.next`.
- `benefit` w `/queues` działa pełnotekstowo (fragment), więc „PORADNIA OKULISTYCZNA”
  łapie też wariant „DLA DZIECI”. Filtrujemy po faktycznej nazwie.
- Kody województw NFZ (01–16, alfabetycznie) ≠ TERYT (TERYT = 2 × NFZ).
- Pola bywają `null`: `dates`, `statistics`, `statistics.computed-data`, `age-range`,
  współrzędne. Współrzędne bywają `0` albo z przecinkiem. Flagi to `"Y"`/`"N"`.
- `dates.date` bywa w przeszłości. `dates.date-situation-as-at` to „stan na” i UI
  **zawsze** go pokazuje.
- Kształt `/many-places/{id}` jest niepewny. Parser szuka obiektów z polami adresowymi rekurencyjnie.
- `/benefits` i `/localities` zwracają (prawdopodobnie) listę stringów. Parser obsługuje też obiekty.
- Parametr `benefitForChildren`: format niepotwierdzony, więc go nie wysyłamy, a wariant
  dla dzieci rozpoznajemy po nazwie świadczenia i polu `benefits-for-children`.
- Środowisko, w którym powstało v1.0, nie miało dostępu do `api.nfz.gov.pl`, Nominatim ani OSRM.
  Fixtures są zrekonstruowane (`tests/fixtures/README.md`).
