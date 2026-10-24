# Termin: plan v1.0

Termin pomaga znaleźć **najszybszy termin u specjalisty na NFZ** i od razu mówi:
gdzie, jak daleko, jak dojechać i pod jaki numer zadzwonić. Odbiorcy to zwykli
ludzie, w tym seniorzy i ich dzieci. Priorytetem są prostota, czytelność i zaufanie.

## Stan wyjściowy

Prompt projektowy zakładał, że w repo jest działające v0.1 (klient NFZ, CLI,
panel z mapą). W rzeczywistości repo zawierało tylko README profilu GitHub
(przeniesione do `docs/PROFILE_README.md`). Po konsultacji z właścicielem
projekt budujemy **od zera**, a funkcje z v0.1 wchodzą w skład Faz 0–2.

Środowisko, w którym powstaje v1.0, **nie ma dostępu sieciowego** do
`api.nfz.gov.pl`, Nominatim ani OSRM (polityka proxy). Konsekwencje opisuje
[ADR-0007](adr/0007-fixtures-bez-dostepu-do-api.md): fixtures są
zrekonstruowane ze schematu API, a `scripts/capture_fixtures.py` i testy
`-m live` służą do weryfikacji na prawdziwym API przy pierwszej okazji.

## Architektura (skrót)

```
termin/
  config.py         pydantic-settings: env TERMIN_* + ~/.config/termin/config.toml
  logging.py        structlog, procesor usuwający dane osobowe
  core/             logika domenowa, bez I/O użytkownika
    models.py       modele Pydantic v2 (QueueEntry, Place, Dates, ...)
    parser.py       odporne parsowanie JSON:API z NFZ
    client.py       asynchroniczny klient NFZ: semafor 4, retry/backoff, cache
    cache.py        cache odpowiedzi HTTP w SQLite (TTL)
    provinces.py    16 oddziałów NFZ, przybliżone bbox-y, sąsiedztwo
    benefits.py     synonimy (data/synonyms.toml) + fuzzy (rapidfuzz)
    geo.py          haversine, geokodowanie Nominatim + offline
    routing.py      czas dojazdu przez OSRM (opcjonalnie)
    ranking.py      tryby sortowania, ostrzeżenia, wyjaśnienia
    search.py       orkiestracja: zapytanie → wyniki z rankingiem
    i18n.py         teksty backendu z plików locales/*.toml
  store/            SQLite: migracje, snapshoty, trendy, subskrypcje, eksport
  notify/           kanały (ntfy, Telegram, e-mail, notify-send), watcher, bot
  cli/              Typer: szukaj, historia, obserwuj, eksport, serwer, ...
  web/              FastAPI (API + statyczny frontend PWA), metryki, rate limit
```

Decyzje architektoniczne są w `docs/adr/`.

## Fazy

| Faza | Zakres | Status |
|------|--------|--------|
| 0 | Fundament: struktura, Pydantic, klient z retry i cache, config, structlog, CI, fixtures | ✅ |
| 1 | Synonimy, fuzzy matching, dzieci/dorośli, geokodowanie (online + offline), haversine, OSRM | ✅ |
| 2 | Ranking (najszybciej / najbliżej / kompromis), many-places, ostrzeżenia, „dlaczego tu”, CLI | ✅ |
| 3 | SQLite, snapshoty, trend placówki, percentyle, mapa cieplna powiatów, eksport CSV/JSON | ⏳ |
| 4 | Subskrypcje, ntfy, Telegram (bot), e-mail, notify-send, scheduler, deduplikacja, PRIVACY.md | ⏳ |
| 5 | Web mobile-first, PWA, WCAG 2.1 AA, duży tekst, skrypt rozmowy, .ics, i18n pl/en/uk | ⏳ |
| 6 | Docker, compose + Caddy, /healthz, /metrics, rate limiting, e2e Playwright + axe, PyPI | ⏳ |
| 7 | README pl/en, CONTRIBUTING, CoC, MIT, szablony issue/PR, 5 × good first issue | ⏳ |

Każda faza kończy się zielonymi `pytest`, `ruff check`, `ruff format --check`
i `mypy --strict termin` oraz commitem w stylu Conventional Commits.

## Ryzyka

| Ryzyko | Wpływ | Mitygacja |
|--------|-------|-----------|
| Brak dostępu do prawdziwego API w trakcie budowy | Parser może nie trafić w szczegóły schematu (`/many-places`, `/benefits`) | Parser tolerancyjny (szuka pól, a nie zakłada kształtu), skrypt do przechwycenia fixtures, testy `live`, ADR-0007 |
| Nazwy świadczeń w słowniku mogą się różnić od nazw NFZ | Brak wyników dla synonimu | Synonimy mapują na **fragment** nazwy (wyszukiwanie pełnotekstowe NFZ), a nie na dokładną nazwę. Wyniki są grupowane po faktycznej nazwie świadczenia. Komenda `termin swiadczenia` pozwala sprawdzić nazwy. |
| Przeciążenie publicznego API NFZ | Blokada, zła reputacja projektu | Maks. 4 równoległe żądania, backoff na 429/5xx, cache w SQLite, limit stron, rate limiting naszego API, grupowanie subskrypcji |
| Nieaktualne dane placówek (aktualizacja ~raz w tygodniu) | Mylące terminy | Zawsze „stan na”, ostrzeżenie > 14 dni, zachęta do telefonu |
| Nominatim/OSRM demo mają ostre limity | Blokada IP | 1 req/s, cache, User-Agent, offline fallback, konfigurowalny URL własnej instancji |
| Brak geometrii powiatów | Mapa cieplna bez wielokątów | Agregacja po kodzie TERYT powiatu, kółka w centroidach placówek i tabela. Wielokąty PRG to zadanie na później. |
| Dane osobowe (lokalizacja, kontakt do powiadomień) | Ryzyko prywatności | Brak kont, losowe tokeny, redakcja logów, `termin dane usun`, PRIVACY.md |

## Odłożone / poza zakresem v1.0

Aktualizowane po każdej fazie. Szczegóły w podsumowaniach commitów.

- Wielokąty powiatów (PRG GUGiK) dla prawdziwej mapy choropletowej.
- Pełna tabela centroidów kodów pocztowych z GeoNames (skrypt jest, danych w repo
  nie ma, bo źródło było niedostępne z sieci budowy).
- Weryfikacja fixtures na prawdziwym API (patrz ADR-0007).
