# ADR-0010: Opcjonalne konta, zgody i warstwa bezpieczeństwa

- Status: przyjęty (zastępuje zasadę „żadnych kont” z planu v1.0)
- Data: 2026-10-25

## Kontekst

Właściciel projektu poprosił o logowanie, pełną zgodność z prawem (RODO, cookies, regulamin),
mocniejsze zabezpieczenia i instalację na telefonie i komputerze. Pierwotnie Termin działał
bez kont, z losowym tokenem subskrypcji.

## Decyzja

- **Konta są opcjonalne.** Wyszukiwanie i obserwowanie dalej działają bez logowania. Konto
  służy do zarządzania obserwacjami z wielu urządzeń, eksportu i usunięcia danych.
  Instancja może wyłączyć konta (`TERMIN_ACCOUNTS_ENABLED=false`).
- Logowanie e-mail + hasło (scrypt z biblioteki standardowej, bez nowych zależności),
  sesje w ciasteczku HttpOnly z tokenem przechowywanym jako skrót, CSRF: `Origin` + nagłówek
  `X-CSRF-Token`. Weryfikacja e-maila i reset hasła tylko, gdy instancja ma SMTP. Bez
  poczty konta działają, ale reset hasła jest niedostępny (komunikat 501).
- **Informacja o szukanym świadczeniu może być daną o zdrowiu** (art. 9 RODO), gdy łączy się
  ją z identyfikowalną osobą (e-mail, czat). Dlatego każda obserwacja wymaga **wyraźnej zgody**
  (checkbox w panelu, `; zgoda` w bocie, `--zgoda` w CLI), a jej data trafia do bazy
  (`consent_at`). Wycofanie zgody = usunięcie obserwacji.
- Pamięć w urządzeniu (art. 399 Prawa komunikacji elektronicznej): niezbędne (sesja, język,
  duży tekst, pliki aplikacji) bez zgody; ostatnie wyniki offline tylko po „Zgadzam się”.
  Service worker nie zapisuje już odpowiedzi API.
- Dokumenty prawne (regulamin, polityka prywatności, cookies, deklaracja dostępności, kontakt)
  jako szablony w `termin/web/legal_docs/`, uzupełniane danymi operatora z konfiguracji.
  Brak danych operatora jest widoczny na stronach i logowany przy starcie.
- Instalacja: PWA (manifest z `id` i skrótami, service worker). Przycisk „Zainstaluj”
  korzysta z `beforeinstallprompt` (Chrome/Edge/Android), a na iOS pokazuje instrukcję.
  Sklepy z aplikacjami nie są potrzebne.

## Konsekwencje

- Baza zawiera teraz adresy e-mail. Kopie zapasowe trzeba szyfrować, a `termin dane usun`
  usuwa też konta.
- Dokumenty prawne są szablonem, nie poradą prawną: przed publicznym uruchomieniem operator
  powinien je skonsultować z prawnikiem i uzupełnić (lista w `docs/DEPLOY.md`).
