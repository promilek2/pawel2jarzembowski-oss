# Jak pomóc w rozwoju Termina

Dzięki, że chcesz pomóc! Termin ma służyć zwykłym ludziom, więc liczy się każda poprawka:
nowy synonim, literówka w tłumaczeniu, test albo zgłoszenie błędu.

Zasady współpracy: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Najprostszy wkład: dodaj synonim świadczenia

Ludzie szukają „laryngologa”, a NFZ pisze „PORADNIA OTORYNOLARYNGOLOGICZNA”. Słownik, który to
tłumaczy, to zwykły plik tekstowy: [termin/data/synonyms.toml](termin/data/synonyms.toml).

1. Sprawdź, jak NFZ nazywa świadczenie: `termin swiadczenia <fragment>` (np. `termin swiadczenia laryng`)
   albo w [oficjalnej wyszukiwarce](https://terminyleczenia.nfz.gov.pl).
2. Dopisz alias do istniejącego wpisu albo dodaj nowy blok:

   ```toml
   [[benefit]]
   id = "proktolog"                      # krótki, stały identyfikator
   label = "Proktolog (poradnia proktologiczna)"
   query = "PORADNIA PROKTOLOGICZNA"      # FRAGMENT nazwy NFZ, wielkimi literami
   aliases = ["proktolog", "proktologia"]
   kind = "poradnia"                      # poradnia | badanie | zabieg | szpital
   # children_query = "... DLA DZIECI"    # jeśli istnieje wariant dziecięcy
   # exclude = ["DLA DZIECI"]             # i wtedy wykluczenie dla dorosłych
   ```

3. Uruchom `pytest tests/test_benefits.py`. Test pilnuje, żeby id i aliasy się nie dublowały.

**Zasada:** aliasy to nazwy świadczenia (potoczne, skróty), **nie objawy ani choroby**.
„migrena → neurolog” byłaby poradą medyczną, a takich nie dajemy. Literówek nie trzeba dopisywać,
bo łapie je fuzzy matching.

## Uruchomienie lokalne

```bash
git clone https://github.com/promilek2/pawel2jarzembowski-oss.git termin && cd termin
uv venv && source .venv/bin/activate      # albo: python -m venv .venv
uv pip install -e ".[dev]"                # albo: pip install -e ".[dev]"

pytest                       # testy (nie wychodzą do sieci)
ruff check . && ruff format --check .
mypy --strict termin

termin szukaj okulista --gdzie Gdynia     # na prawdziwym API NFZ
python -m tests.e2e.server                # panel z FIKCYJNYMI danymi: http://127.0.0.1:8765
```

Testy przeglądarkowe (Playwright + axe-core):

```bash
uv pip install -e ".[e2e]" && playwright install chromium
pytest -m e2e
```

Testy na prawdziwym API NFZ: `pytest -m live`. Aktualizacja fixtures z prawdziwych odpowiedzi:
`python scripts/capture_fixtures.py`.

## Zasady dla kodu

- Każdy PR przechodzi: `pytest`, `ruff check`, `ruff format --check`, `mypy --strict termin`.
- Testy nigdy nie łączą się z siecią. Używaj `tests/fake_nfz.py` (`httpx.MockTransport`).
- Komunikaty dla użytkownika piszemy po polsku, prosto i bez żargonu. Nowe teksty interfejsu
  dodawaj do **wszystkich** plików `termin/web/static/i18n/*.json` (pilnuje tego test).
- Nie loguj danych osobowych (lokalizacja, e-mail, czat, token). Patrz `termin/logs.py`.
- Nie dodawaj trackingu ani zasobów z CDN. Nie używaj logo NFZ.
- Większe decyzje opisz krótkim ADR-em w `docs/adr/` (szablon: `0000-szablon.md`).
- Commity w stylu [Conventional Commits](https://www.conventionalcommits.org/pl/): `feat:`, `fix:`, `docs:`…

## Tłumaczenia

Teksty panelu są w `termin/web/static/i18n/` (pl, en, uk). Teksty CLI i powiadomień są
w `termin/locales/pl.toml`. Nowy język: skopiuj `pl.json`, przetłumacz i dodaj go do listy
`LANGS` w `app.js` oraz do przełącznika w `index.html`.

## Zgłaszanie błędów

Użyj szablonu issue. Przy błędnych danych (np. zły telefon placówki) pamiętaj, że pochodzą z NFZ.
Możemy najwyżej poprawić sposób ich wyświetlania.
