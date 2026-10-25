# ADR-0012: Funkcje dla zalogowanych: zgłoszenia z telefonu, „Moje sprawy”, Web Push

- Status: przyjęty
- Data: 2026-10-25

## Kontekst

Największa słabość danych NFZ to nieaktualność: placówki raportują terminy mniej więcej
raz w tygodniu. Użytkownicy dzwonią i wiedzą, jak jest naprawdę. Chcieliśmy zebrać tę wiedzę
bez otwartych komentarzy (moderacja, zniesławienie, dane osobowe lekarzy, obowiązki DSA,
sprzeczność z zasadą „nie oceniamy, gdzie leczą lepiej”). Do tego seniorzy i ich rodziny
potrzebują miejsca na skierowania i wizyty, a powiadomienia przez ntfy wymagają osobnej aplikacji.

## Decyzja

- Wszystkie trzy funkcje są **tylko dla zalogowanych**. Przyciski widzi każdy; bez konta
  otwierają logowanie.
- **„Sprawdziłem telefonicznie”**: ustrukturyzowane zgłoszenie bez wolnego tekstu
  (dodzwoniłem się? termin taki sam / inny (data) / brak miejsc). Jedno zgłoszenie na osobę,
  kolejkę i dzień (poprawka nadpisuje), maks. 30 dziennie na konto. Przy wyniku pokazujemy
  zbiorczo dane z 14 dni (liczby i medianę różnicy dni), nigdy pojedyncze zgłoszenia.
- **„Moje sprawy”**: sprawy ze statusem (do zadzwonienia, czekam, zapisany, zakończona,
  odmowa), datą wizyty (`.ics`) i notatką; **prywatne notatki** przy poradni (klucz: id
  kolejki NFZ). Widzi je tylko właściciel.
- **Zgoda art. 9 RODO** (`users.health_consent_at`) przed pierwszym zapisem sprawy, notatki
  albo zgłoszenia (API zwraca 428, frontend pokazuje okno zgody i ponawia). Wycofanie zgody
  usuwa te dane. Obserwacje mają własną zgodę (ADR-0010).
- **Web Push** bez pośredników: szyfrowanie RFC 8291 i VAPID (RFC 8292) z biblioteką
  `cryptography`. Klucz VAPID z `TERMIN_VAPID_PRIVATE_KEY` albo generowany i zapisany w bazie.
  Adres urządzenia podaje przeglądarka, więc wysyłamy tylko do znanych usług push (FCM,
  Mozilla, Apple, Windows) przez HTTPS bez przekierowań (ochrona przed SSRF). 404/410 usuwa
  urządzenie. Kanał `push` w obserwacjach (cel = id konta) i przypomnienia o jutrzejszych
  wizytach z „Moich spraw” (obserwator, 8–21 czasu polskiego, raz na wizytę).
- Kod konta i tych funkcji jest w osobnym module `account.js` ładowanym leniwie
  (`import()`), żeby pierwszy pakiet zmieścił się w budżecie 150 KB.

## Konsekwencje

- Zgłoszenia mogą być fałszywe. Ograniczenia: konto, jedno zgłoszenie dziennie na kolejkę,
  limit dzienny, pokazujemy tylko zbiorcze liczby z datą. Przy nadużyciach można dodać
  wymóg potwierdzonego e-maila.
- Usługi push (Google, Mozilla, Apple, Microsoft) widzą adres IP urządzenia i fakt wysłania
  powiadomienia, ale nie treść (jest zaszyfrowana kluczem urządzenia). Opisane w polityce prywatności.
- Na darmowym Render baza i klucz VAPID znikają przy wdrożeniu; frontend po cichu odnawia
  subskrypcję, gdy klucz serwera się zmienił.
- Nowa zależność: `cryptography`.
