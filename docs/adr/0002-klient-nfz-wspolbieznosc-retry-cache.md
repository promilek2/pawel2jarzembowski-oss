# ADR-0002: Klient NFZ: współbieżność, retry, cache

- Status: przyjęty
- Data: 2026-10-24

## Kontekst

API NFZ jest publiczne i darmowe. Nie chcemy go przeciążać. Wyszukiwanie w
promieniu wymaga kilku województw i wielu stron (limit 25 rekordów na stronę).

## Decyzja

- `httpx.AsyncClient` opakowany w `NfzClient` z `asyncio.Semaphore(4)`
  (konfigurowalne, ale ograniczone do 1–4).
- Retry z wykładniczym backoffem i jitterem na 429, 5xx i błędy sieci. Respektujemy
  nagłówek `Retry-After`. Maksymalnie 4 próby.
- `User-Agent: termin/<wersja> (+https://github.com/promilek2/pawel2jarzembowski-oss)`.
- Cache odpowiedzi w SQLite (`termin/core/cache.py`), klucz = ścieżka + posortowane
  parametry. TTL: kolejki 6 h, słowniki (`/benefits`, `/localities`) 7 dni.
- Limit stron na jedno zapytanie (domyślnie 40 = 1000 rekordów).
- Transport jest wstrzykiwany, więc testy używają `httpx.MockTransport` i nigdy
  nie wychodzą do sieci.

Własna implementacja retry (kilkanaście linii) zamiast `tenacity`: mniej
zależności, łatwiej testować z wstrzykiwanym `sleep`.

## Konsekwencje

- Powtórne wyszukiwania są natychmiastowe i nie obciążają NFZ.
- Dane mogą być nieświeże o TTL. Akceptowalne, bo placówki aktualizują dane
  mniej więcej raz w tygodniu.
