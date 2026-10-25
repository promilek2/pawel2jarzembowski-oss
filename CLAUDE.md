# CLAUDE.md: kontekst dla kolejnych sesji

**Termin**: open-source'owa aplikacja (Python 3.11+) do szukania najszybszego terminu u
specjalisty na NFZ. Dane pochodzą z publicznego API NFZ „Terminy leczenia”. Projekt nie
jest oficjalną aplikacją NFZ: bez logo NFZ, bez porad medycznych, bez trackingu.

## Komendy

```bash
uv venv && uv pip install -e ".[dev]"     # środowisko
pytest                                     # testy (bez sieci; live i e2e pomijane)
pytest -m live                             # kontrakt na prawdziwym API NFZ
pytest -m e2e                              # Playwright + axe (wymaga .[e2e]; TERMIN_CHROMIUM=ścieżka)
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
  `termin/web/`: FastAPI + statyczny frontend.
- `termin/web/app.py`: `create_app()` (FastAPI, lifespan tworzy `Runtime`, opcjonalnie watcher).
  API: `/api/search|suggest|config|history|stats|export.{csv,json}|ics|subscriptions`,
  strony `/stop/{token}` (GET pokazuje formularz, POST usuwa). Nagłówki bezpieczeństwa i CSP
  (`script-src 'self'`, bez inline JS/CSS, więc w testach Playwright nie używaj `wait_for_function`).
- Frontend `termin/web/static/`: `index.html`, `app.js` (moduł ES, DOM przez textContent),
  `style.css`, `i18n/{pl,en,uk}.json` (klucze muszą być zgodne, pilnuje tego test),
  `sw.js`, `manifest.webmanifest`, Leaflet w `vendor/` (ładowany leniwie). Budżet własnego
  kodu 150 KB (test). Serwer z atrapą NFZ do pracy nad UI: `python -m tests.e2e.server`.
- Wygląd: kierunek „Numerek” (ADR-0009): wyniki jako bilety z perforacją, ciemny panel
  wyszukiwarki, tokeny kolorów w `:root` w `style.css`, krój Bricolage Grotesque w
  `vendor/bricolage/` (OFL). Pomarańcz `--signal` zawsze z atramentowym tekstem, nigdy jako tekst.
- `termin/notify/`: `channels.py` (ntfy JSON, Telegram, SMTP, notify-send), `message.py`
  (treść), `subscribe.py` (tworzenie subskrypcji, wspólne dla CLI/web/bota),
  `watcher.py` (grupowanie po zapytaniu, deduplikacja „tylko lepszy termin”, cooldown),
  `telegram_bot.py` (long polling). Subskrypcje: `termin/store/subscriptions.py`
  (token, współrzędne zaokrąglone do 0,01°, `wipe_user_data`).
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

## Pułapki API NFZ (zweryfikuj na żywym API, patrz ADR-0007 i ADR-0013)

- **Od 10.07.2026 API 1.4**: `https://apinfz.nfz.gov.pl/app-itl-api-pcus`, `format=json`,
  bez `api-version`, limit 10 zapytań / 10 s (limiter w `client.py`). Stare 1.3:
  `https://api.nfz.gov.pl/app-itl-api` z `api-version=1.3`.
- **Od 1.07.2026 brak pierwszego wolnego terminu**: `dates.pcus` (tekst „1 mies. 3 tyg.”,
  `text.parse_pcus`). Używaj `entry.expected_date(today)` / `RankedResult.expected_date`
  (dziś + PCUŚ), nie `first_date`. UI zawsze mówi „prognoza NFZ”.
- **Nowe nazwy**: „ŚWIADCZENIA Z ZAKRESU OKULISTYKI” zamiast „PORADNIA OKULISTYCZNA” itd.;
  wariant dla dzieci = ta sama nazwa + flaga. Test `test_every_synonym_matches_real_nfz_names`.
- W 1.4 zwykle brak współrzędnych → `search.with_approx_location` (środek gminy z TERYT).
  `queue-in-cer` = zapisy przez IKP. PCUŚ „0 dni” + 0 oczekujących = pusta kolejka.
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

## Konta, prawo, bezpieczeństwo (ADR-0010)

- Konta opcjonalne: `termin/store/accounts.py` (użytkownicy, sesje, tokeny e-mail jako skrót
  SHA-256), `termin/security.py` (scrypt, polityka haseł, e-mail), `termin/web/auth.py`
  (API `/api/auth/*`, `/api/account/*`, ciasteczko sesji, `csrf_guard`). Zapytania
  zmieniające stan z sesją wymagają nagłówka `X-CSRF-Token` (wartość z `/api/auth/me`).
- Odzyskiwanie i 2FA (ADR-0011): kody odzyskiwania (`recovery_codes`, SHA-256), TOTP w
  `security.py` (bez zależności; QR przez `segno` jako macierz rysowana na `<canvas>`),
  logowanie dwuetapowe przez `login_challenges` (`start_login` w `auth.py`: reset hasła nigdy
  nie omija 2FA). Sesje mają `device` (zgrubny opis, bez UA i IP) i `persistent`.
- Funkcje dla zalogowanych (ADR-0012): `termin/store/personal.py` (zgłoszenia z telefonu,
  sprawy, notatki, zgoda art. 9 `health_consent_at`; zapis bez zgody → 428), `termin/web/personal.py`
  (API `/api/me/*`, `/api/reports*`, `/api/cases*`, `/api/notes*`, `/api/push/*`),
  `termin/notify/webpush.py` (RFC 8291 + VAPID, lista `PUSH_HOSTS` przeciw SSRF, kanał `push`),
  przypomnienia o wizytach w `watcher.py`. Frontend: kod konta w leniwym `static/account.js`
  (importuje helpery z `app.js`; budżet 40 KB w teście).
- Obserwacja wymaga zgody (`consent`): API, `--zgoda` w CLI, `; zgoda` w bocie. Bez niej
  `SubscribeError`.
- Strony prawne: szablony `termin/web/legal_docs/*.html` (string.Template, `$zmienne`),
  renderowane przez `termin/web/legal.py` z danymi `TERMIN_OPERATOR_*`.
- Frontend: wyniki offline (`termin:last`) tylko po zgodzie z banera (`termin:consent`).
  Service worker nie cache'uje API.

## Wdrożenie i narzędzia

- `Dockerfile` (non-root UID 10001, healthcheck `/healthz`, opcjonalny sekret `ca` dla proxy
  z własnym CA), `docker-compose.yml` (+ profil `https` z Caddy, `deploy/Caddyfile`).
  Szczegóły: `docs/DEPLOY.md`.
- `/metrics` (Prometheus, `termin/metrics.py`), rate limiting slowapi (`TERMIN_RATE_LIMIT`,
  `TERMIN_RATE_LIMIT_LIGHT`).
- Ochrona przed DoS: `termin/web/protection.py` (globalny limiter IP, `MailThrottle`,
  `BodyLimitMiddleware`), semafor wyszukiwań w `app.py` (503/504), pula hashowania w
  `security.py` (`BusyError` → 503). Model zagrożeń i wyniki `scripts/load_test.py`:
  `docs/SECURITY_REVIEW.md`. Port 8000 nigdy publicznie (uvicorn nie ma timeoutu nagłówków),
  zawsze przez Caddy.
- CI: `.github/workflows/ci.yml` (lint, mypy, testy 3.11–3.13, pokrycie core ≥ 85%, e2e,
  Docker). Wydanie: `release.yml` (tag `vX.Y.Z` → PyPI przez Trusted Publishing).
- Skrypty: `scripts/capture_fixtures.py` (prawdziwe odpowiedzi NFZ), `build_postal_centroids.py`
  (GeoNames), `make_icons.py` (ikony PWA), `screenshots.py` (zrzuty do README).
- Zadania dla nowych osób: `docs/issues/`. Dokumenty: `README.md`, `README.en.md`,
  `CONTRIBUTING.md`, `PRIVACY.md`, `docs/DATA_SOURCES.md`.

## Do zrobienia w pierwszej kolejności

Zweryfikować fixtures i synonimy na prawdziwym API NFZ (ADR-0007, `docs/PLAN.md` → „Odłożone”).
