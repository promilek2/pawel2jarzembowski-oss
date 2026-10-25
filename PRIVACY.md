# Prywatność w Termin

Termin nie używa analityki ani trackerów stron trzecich i nie sprzedaje danych. Konto jest
**opcjonalne**: z wyszukiwarki i obserwowania można korzystać bez logowania. Ten dokument
opisuje, co przechowuje **instancja** Termina (Twój komputer albo serwer, na którym ktoś go
uruchomił). Wiążąca dla użytkowników instancji jest jej polityka prywatności pod adresem
`/prywatnosc` (szablon: `termin/web/legal_docs/prywatnosc.html`).

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

## Konto (opcjonalne)

Przechowujemy: adres e-mail, skrót hasła (scrypt z solą), daty rejestracji, akceptacji
regulaminu, potwierdzenia adresu i ostatniego logowania. Sesja to losowy token w ciasteczku
HttpOnly; w bazie jest tylko jego skrót oraz zgrubny opis urządzenia („Chrome · Android”), żeby
lista „Zalogowane urządzenia” była czytelna (bez adresu IP i pełnego identyfikatora przeglądarki).
Kody odzyskiwania trzymamy tylko jako skrót SHA-256. Jeśli włączysz weryfikację dwuetapową,
zapisujemy sekret do generowania kodów (bez niego serwer nie sprawdzi kodu). Konto usuniesz sam (razem z obserwacjami), a dane
pobierzesz jako JSON („Pobierz moje dane”). Konta z niepotwierdzonym e-mailem usuwamy po 7 dniach.

## Funkcje dla zalogowanych

„Moje sprawy”, prywatne notatki przy poradniach i zgłoszenia „Sprawdziłem telefonicznie”
zapisujemy tylko po zalogowaniu i za wyraźną zgodą (art. 9 RODO), bo mówią, do jakiego
specjalisty się zapisujesz. Widzisz je tylko Ty. Ze zgłoszeń inni widzą wyłącznie zbiorcze
liczby z 14 dni. Wycofanie zgody (w „Moich sprawach”) usuwa te dane.

Powiadomienia w telefonie (Web Push) wysyłamy przez usługę push Twojej przeglądarki (Google,
Mozilla, Apple albo Microsoft). Treść jest zaszyfrowana kluczem urządzenia, więc dostawca jej
nie odczyta; zna adres IP urządzenia i fakt doręczenia. Wyłączysz je w koncie.

## Obserwowanie (powiadomienia)

Informacja, jakiego świadczenia szukasz, może dotyczyć zdrowia, dlatego obserwacja wymaga
**wyraźnej zgody** (art. 9 ust. 2 lit. a RODO). Datę zgody zapisujemy razem z obserwacją.

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
- konto i wszystkie powiązane obserwacje: „Usuń konto” w panelu,
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
| Anthropic API (opcjonalnie) | pytanie do pomocnika „Jak to działa?”, gdy instancja ma klucz `TERMIN_AI_API_KEY` | treść pytania i wcześniejszych wiadomości tej rozmowy |

### Pomocnik „Jak to działa?”

Okienko „? Pomoc” (w nagłówku) odpowiada na pytania o obsługę aplikacji. Bez klucza API odpowiada z
wbudowanej bazy (`termin/data/assistant_faq.toml`) i nic nie wychodzi poza serwer. Z kluczem
pytanie trafia do Anthropic (model Claude); przed pierwszym pytaniem pokazujemy o tym
informację. Rozmowy nie zapisujemy ani w bazie, ani w przeglądarce, a jej treści nie logujemy
(`messages`, `question`, `reply` są w `SENSITIVE_KEYS`). Pomocnik nie udziela porad medycznych.

## Panel web

Brak ciasteczek śledzących i analityki. Ciasteczko sesji powstaje tylko po zalogowaniu.
Preferencje (język, tryb „duży tekst”, motyw) zapisujemy w pamięci przeglądarki. Ostatnie wyniki
offline zapisujemy wyłącznie po wyrażeniu zgody w banerze (szczegóły: `/cookies`). Ograniczenie liczby zapytań (rate limiting) korzysta z adresu IP
tylko w pamięci procesu, bez zapisu.
