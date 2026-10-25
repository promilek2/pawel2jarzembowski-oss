# ADR-0013: API NFZ 1.4, prognoza PCUŚ zamiast terminu, nowe nazwy świadczeń

- Status: przyjęty
- Data: 2026-10-25

## Kontekst

Użytkownik zgłosił: „okulista, Gdynia, 200 km i żaden lekarz”. Przyczyny (zmiany NFZ z lipca 2026,
po zaprojektowaniu v1.0 na zrekonstruowanych danych, ADR-0007):

1. Część poradni zmieniła nazwy: „PORADNIA OKULISTYCZNA” → „ŚWIADCZENIA Z ZAKRESU OKULISTYKI”
   (także endokrynologia, neurologia, ortopedia, otolaryngologia, urologia, gastroenterologia,
   diabetologia, nefrologia, onkologia, choroby płuc). Wariant dla dzieci ma tę samą nazwę i flagę
   `benefits-for-children`. 17 z 40 wpisów słownika nie trafiało w nic.
2. Od 1.07.2026 NFZ nie publikuje pierwszego wolnego terminu (`dates.date`), tylko prognozę
   PCUŚ tekstem (`dates.pcus`, np. „1 mies. 3 tyg.”), liczoną zwykle raz w miesiącu.
3. Od 10.07.2026 obowiązuje API 1.4: `https://apinfz.nfz.gov.pl/app-itl-api-pcus`, limit
   10 zapytań na 10 s, flaga `queue-in-cer`, w większości rekordów brak współrzędnych
   (część ma zamienioną szerokość z długością).
4. Pierwsza wizyta u kardiologa, mammografia i test HPV są w Centralnej e-Rejestracji (IKP)
   i nie mają kolejek w API.

Prawdziwy kształt API 1.4 i listę 476 nazw (stan 2026-10-20) wzięliśmy z publicznej migawki
projektu Proclever/gdzie-czeka-sie-najkrocej (MIT), bo nasze środowisko nadal nie ma dostępu
do api.nfz.gov.pl.

## Decyzja

- Słownik synonimów poprawiony do prawdziwych nazw; test porównuje każdy fragment z listą
  `tests/fixtures/nfz_benefit_names_2026-10.txt`. Pole `note` dla świadczeń bez kolejek
  (CeR: kardiolog, mammografia; USG, echo, Holter, densytometria, sanatorium).
- `QueueEntry.expected_date(today)`: pierwszy wolny termin albo **dziś + PCUŚ** (PCUŚ to
  oczekiwanie dla kogoś, kto zapisuje się teraz). `is_forecast` zawsze widoczne w UI („~5 dni,
  prognoza NFZ”), w powiadomieniach i w skrypcie rozmowy; ostrzeżenie `forecast`.
- Pusta kolejka (PCUŚ „0 dni”, zero oczekujących) to zwykle nieczynna kolejka: ostrzeżenie
  `empty_queue`, niżej w rankingu, poza medianami i bez powiadomień.
- Kolejki w CeR: ostrzeżenie `cer` z informacją o IKP zamiast „brak terminu”.
- Brak współrzędnych: środek gminy z kodu TERYT (`termin/data/gminy.csv`, PRG GUGiK przez
  projekt MIT), dla dzielnic miast średnia powiatu; ostrzeżenie `approx_location`, „~” przy km.
- Klient: domyślnie API 1.4 bez `api-version`, limiter okna 10 s (`TERMIN_NFZ_REQUESTS_PER_10S`).
  Powrót do 1.3: `TERMIN_NFZ_BASE_URL=https://api.nfz.gov.pl/app-itl-api TERMIN_NFZ_API_VERSION=1.3`.
- Lokalny filtr nazw: wynik ze słownika musi zawierać fragment (na wypadek szerszego
  dopasowania pełnotekstowego albo zignorowanego parametru `benefit`).

## Konsekwencje

- Wyniki to prognozy, nie wolne terminy; aplikacja mówi to wprost i zachęca do telefonu
  (tym cenniejsze są zgłoszenia „Sprawdziłem telefonicznie”, ADR-0012).
- Nadal trzeba potwierdzić na żywym API, że 1.4 obsługuje parametr `benefit`; jeśli nie,
  lokalny filtr zadziała, ale każde wyszukiwanie pobierze więcej stron.
