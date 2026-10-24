# ADR-0006: Geokodowanie i odległości

- Status: przyjęty
- Data: 2026-10-24

## Kontekst

Użytkownik podaje adres, kod pocztowy albo GPS. Musimy wyliczyć odległość
do placówek i ustalić, które województwa odpytać.

## Decyzja

- Online: Nominatim (OSM), `countrycodes=pl`, maks. 1 req/s (blokada globalna
  w procesie), własny `User-Agent`, cache w SQLite bez wygasania (adresy się
  nie przesuwają). URL konfigurowalny.
- Offline: wbudowana tabela centroidów największych miejscowości
  (`termin/data/places.csv`) i parser „kod pocztowy + miejscowość”. Skrypt
  `scripts/build_postal_centroids.py` generuje pełną tabelę kodów pocztowych
  z GeoNames (CC BY 4.0). Szczegóły i licencje w `docs/DATA_SOURCES.md`.
- Odległość domyślnie po prostej (haversine). Opcjonalnie czas dojazdu autem z
  OSRM (`/table`, jedno żądanie na wyszukiwanie, URL konfigurowalny, domyślnie
  wyłączone, bo publiczny demo-serwer jest tylko do małego ruchu).
- Wybór województw do odpytania: województwo punktu + każde, którego
  przybliżony bbox przecina okrąg o zadanym promieniu. Bbox-y są zawyżone,
  więc odpytujemy raczej za dużo niż za mało.

## Konsekwencje

- Aplikacja działa bez Nominatim dla większości realnych zapytań (miasta).
- Przybliżone bbox-y mogą powodować zbędne zapytania przy granicy. To akceptowalny koszt.
