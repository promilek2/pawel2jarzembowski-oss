# ADR-0011: Odzyskiwanie dostępu bez poczty, weryfikacja dwuetapowa, lista sesji

- Status: przyjęty
- Data: 2026-10-25

## Kontekst

Reset hasła działał tylko przez e-mail, a najprostsze wdrożenie (darmowy Render, ADR brak)
nie ma poczty. Kto zapomniał hasła, tracił konto. Konto z danymi o zdrowiu (art. 9 RODO)
warto też chronić czymś więcej niż hasłem, a użytkownik powinien widzieć, gdzie jest zalogowany.

## Decyzja

- **Kody odzyskiwania**: 10 jednorazowych kodów po 80 bitów (`K7QM-3XRA-PL9D-W2HF`, alfabet
  bez 0/O/1/I) pokazywanych raz po rejestracji, z kopiowaniem i pobraniem pliku. W bazie tylko
  SHA-256 (przy 80 bitach odgadnięcie ze skrótu jest niewykonalne, więc wolny KDF nie jest
  potrzebny). Kod pozwala ustawić nowe hasło (`/api/auth/recover`) albo zastąpić kod z
  aplikacji przy logowaniu. Nieudane próby liczą się do blokady konta.
- **Weryfikacja dwuetapowa TOTP** (RFC 6238, SHA-1, 6 cyfr, 30 s, okno ±1 krok) w bibliotece
  standardowej. Kod QR rysuje frontend na `<canvas>` z macierzy od serwera (biblioteka `segno`,
  czysty Python), więc CSP zostaje bez zmian. Na telefonie działa link `otpauth://`.
  Każdy kod działa raz (`totp_last_step`). Włączenie wymaga hasła i kodu, wyłączenie hasła
  i kodu (albo kodu odzyskiwania).
- **Logowanie w dwóch krokach**: po poprawnym haśle serwer zwraca jednorazowe wyzwanie (5 min,
  5 prób), sesja powstaje dopiero po drugim składniku. Reset hasła (e-mailem albo kodem) nigdy
  nie omija weryfikacji dwuetapowej: po resecie trzeba podać drugi składnik.
- **Sesje**: lista urządzeń z opisem „przeglądarka · system” (bez pełnego User-Agent i bez IP),
  wylogowanie pojedynczego urządzenia. „Nie wylogowuj mnie” (domyślnie włączone): bez niego
  ciasteczko sesyjne i maks. 12 h w bazie.
- **Powiadomienia e-mail** (gdy działa poczta) o zmianie hasła, adresu, kodów i 2FA.
- Zmiana adresu e-mail wymaga hasła i ponownego potwierdzenia.

Nie wprowadzamy (na razie) kluczy dostępu (passkeys/WebAuthn): wymagają stałej domeny i
większej biblioteki; to naturalny następny krok.

## Konsekwencje

- Bez poczty jedyną drogą odzyskania konta są kody. Kto ich nie zapisał i zapomni hasła,
  musi założyć nowe konto (obserwacje bez konta działają dalej przez link `/stop/`).
- Sekret TOTP jest w bazie jawnie (serwer musi go znać). Kopie bazy trzeba szyfrować (już
  wymagane przez ADR-0010).
- Nowa zależność: `segno` (BSD, bez zależności).
