# Prywatność w Termin

Termin nie ma kont użytkowników, nie używa analityki ani trackerów stron trzecich i
nie sprzedaje danych. Ten dokument opisuje, co przechowuje **instancja** Termina
(Twój komputer albo serwer, na którym ktoś go uruchomił).

## Wyszukiwanie

| Dane | Czy zapisujemy? | Szczegóły |
|------|-----------------|-----------|
| Czego szukasz (np. „okulista”) | Nie, w żadnej postaci powiązanej z Tobą | W snapshotach zapisujemy tylko fragment nazwy świadczenia wysłany do NFZ (np. `PORADNIA OKULISTYCZNA`) i województwo, żeby liczyć historię kolejek. |
| Gdzie jesteś (adres, kod, GPS) | Nie wprost | Wynik geokodowania Nominatim trzymamy w cache pod **skrótem SHA-256** adresu, z etykietą na poziomie miejscowości. Współrzędnych GPS nie zapisujemy wcale. |
| Wyniki NFZ | Tak | To publiczne dane placówek (terminy, adresy, telefony), zapisywane jako snapshoty do historii i statystyk. |
| Logi | Bez danych osobowych | Procesor logów usuwa lokalizację, adresy kontaktowe, tokeny i IP (`termin/logs.py`). Biblioteka HTTP nie loguje pełnych adresów URL. |

Adres wpisany do wyszukiwarki jest wysyłany do usługi geokodowania Nominatim
(OpenStreetMap Foundation), chyba że instancja działa w trybie `TERMIN_GEOCODER=offline`
albo korzysta z własnego serwera Nominatim.

## Obserwowanie (powiadomienia)

Subskrypcja to jedyne miejsce, gdzie przechowujemy dane, które **sam podajesz**:

- co obserwujesz (świadczenie, wariant, przypadek, data „przed”),
- obszar: województwo albo punkt z promieniem; współrzędne zaokrąglamy do 0,01° (~1 km),
- kanał i cel powiadomień: temat ntfy, ID czatu Telegram albo adres e-mail,
- datę ostatniego zgłoszonego terminu (żeby nie wysyłać tego samego).

Subskrypcja jest identyfikowana **losowym tokenem** (128 bitów). Nie ma loginu ani hasła.
Temat ntfy działa jak hasło: wybierz trudny do zgadnięcia.

### Jak usunąć dane

- jedna obserwacja: link „Przestań obserwować” w każdym powiadomieniu (`/stop/<token>`),
  `/stop <token>` w bocie Telegram albo `termin obserwuj usun <token>`,
- wszystkie Twoje obserwacje w Telegramie: `/stop wszystko`,
- **wszystkie dane użytkowników w instancji** (administrator): `termin dane usun`.
  Z opcją `--z-historia` znikają też snapshoty NFZ i cache.

## Usługi zewnętrzne

| Usługa | Kiedy | Co dostaje |
|--------|-------|-----------|
| API NFZ | każde wyszukiwanie (poza cache) | parametry zapytania (świadczenie, województwo), bez danych użytkownika |
| Nominatim (OSM) | wpisanie adresu | tekst adresu |
| OSRM (opcjonalnie) | czas dojazdu | współrzędne punktu startu i placówek |
| Kafelki mapy (OSM) | otwarcie mapy w panelu | standardowe żądania kafelków (IP, zakres mapy) |
| ntfy / Telegram / SMTP | wysłanie powiadomienia | treść powiadomienia i cel |

## Panel web

Brak ciasteczek śledzących i analityki. Preferencje (język, tryb „duży tekst”, ostatnie
wyniki offline) zapisujemy wyłącznie w pamięci Twojej przeglądarki (`localStorage`,
cache service workera). Ograniczenie liczby zapytań (rate limiting) korzysta z adresu IP
tylko w pamięci procesu, bez zapisu.
