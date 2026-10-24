# Źródła danych i licencje

| Dane | Źródło | Licencja / warunki | Jak używamy |
|------|--------|--------------------|-------------|
| Terminy, placówki, statystyki kolejek | API NFZ „Terminy leczenia” `https://api.nfz.gov.pl/app-itl-api` | Otwarte dane publiczne NFZ (ponowne wykorzystywanie informacji sektora publicznego) | Zapytania online z cache, maks. 4 równoległe żądania, `User-Agent` z linkiem do projektu. W UI zawsze „stan na” i odesłanie do oficjalnej wyszukiwarki NFZ. |
| Geokodowanie adresów | Nominatim (OpenStreetMap) `https://nominatim.openstreetmap.org` | Dane © współtwórcy OpenStreetMap, ODbL 1.0. [Polityka użycia Nominatim](https://operations.osmfoundation.org/policies/nominatim/): maks. 1 req/s, własny User-Agent, cache. | Tylko gdy użytkownik wpisze adres. Wynik cache'ujemy w SQLite. Można wskazać własną instancję (`TERMIN_NOMINATIM_URL`). |
| Czas dojazdu | OSRM (`TERMIN_OSRM_URL`) | Silnik BSD-2. Dane OSM, ODbL. Publiczny serwer demo tylko do małego ruchu. | Domyślnie wyłączone. Jedno żądanie `table` na wyszukiwanie. |
| Kafelki mapy | OpenStreetMap `tile.openstreetmap.org` (konfigurowalne `TERMIN_TILE_URL`) | ODbL, [polityka kafelków OSM](https://operations.osmfoundation.org/policies/tiles/) | Tylko po kliknięciu „Pokaż mapę”. Atrybucja na mapie. Przy większym ruchu użyj własnego serwera kafelków. |
| Miejscowości offline (`termin/data/places.csv`) | Współrzędne centrów ~160 miejscowości zebrane ręcznie (przybliżone, ±2 km) | Fakty geograficzne. Plik udostępniamy na licencji projektu (MIT). | Zapasowy geokoder, gdy Nominatim jest niedostępny albo wyłączony (`TERMIN_GEOCODER=offline`). |
| Kody pocztowe offline (opcjonalnie) | GeoNames `PL.zip`, generowane przez `scripts/build_postal_centroids.py` | CC BY 4.0, wymagane podanie źródła: „GeoNames, https://www.geonames.org/” | Plik nie jest w repo. Generujesz go lokalnie do `<data_dir>/postal_codes.csv`. |
| Granice województw (`termin/core/provinces.py`) | Przybliżone prostokąty ograniczające, zebrane ręcznie | Fakty geograficzne, MIT | Wybór województw do odpytania przy wyszukiwaniu w promieniu (zawyżone celowo). |
| Leaflet (`termin/web/static/vendor/leaflet/`) | npm `leaflet@1.9.4` | BSD-2-Clause | Mapa w panelu web, hostowana lokalnie (bez CDN). |

## Czego nie robimy

- Nie scrapujemy terminyleczenia.nfz.gov.pl, bo korzystamy z API.
- Nie używamy logo NFZ i nie sugerujemy oficjalności.
- Nie wysyłamy danych użytkownika do analityki stron trzecich.

## Do zrobienia

- Wielokąty powiatów (PRG, GUGiK; dane publiczne) do prawdziwej mapy choropletowej w trendach.
