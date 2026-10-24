# Uzupełnij offline'ową listę miejscowości o siedziby powiatów

Etykiety: `good first issue`, `dane`

## Kontekst
Gdy Nominatim jest niedostępny (albo instancja działa z `TERMIN_GEOCODER=offline`), Termin
rozpoznaje miejscowości z pliku `termin/data/places.csv` (~160 miast). Mieszkańcy mniejszych
miast powiatowych muszą wtedy wpisać najbliższe duże miasto.

## Do zrobienia
- Dopisz brakujące siedziby powiatów (jest ich ok. 380) w formacie `nazwa,kod_NFZ,lat,lon`.
  Kody województw NFZ (01–16) są w `termin/core/provinces.py`.
- Współrzędne weź z OpenStreetMap (centrum miejscowości, 4 miejsca po przecinku).
- Jeśli korzystasz z innego źródła, sprawdź licencję i dopisz je do `docs/DATA_SOURCES.md`.

## Gotowe, gdy
- przechodzi nowy test w `tests/test_geo.py`, który sprawdza, że każda miejscowość leży w bbox
  swojego województwa (`PROVINCES[kod].bbox_distance_km(lat, lon) == 0`),
- `termin szukaj okulista --gdzie Kościan` działa offline.
