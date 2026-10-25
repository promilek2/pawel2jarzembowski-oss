# Bezpieczeństwo

## Zgłaszanie podatności

Jeśli znajdziesz podatność, **nie zgłaszaj jej publicznie w issues**. Zamiast tego:

- użyj prywatnego zgłoszenia na GitHubie: zakładka *Security* → *Report a vulnerability*,
- albo napisz na adres z `/.well-known/security.txt` danej instancji.

Potwierdzimy zgłoszenie w ciągu 3 dni roboczych i poinformujemy o planie naprawy. Prosimy o
odpowiedzialne ujawnienie: daj nam do 90 dni na poprawkę, zanim opublikujesz szczegóły.
Nie testuj podatności na cudzych kontach ani w sposób, który zakłóca działanie usługi
(DoS, masowe zapytania do API NFZ).

## Wspierane wersje

Poprawki bezpieczeństwa trafiają do najnowszej wersji z gałęzi `main` i najnowszego wydania na PyPI.

## Zabezpieczenia w aplikacji

| Obszar | Rozwiązanie |
|--------|-------------|
| Hasła | scrypt (N=2^15, r=8, p=1) z losową solą, porównanie w stałym czasie, minimalna długość 10, odrzucanie popularnych haseł |
| Sesje | losowy token 256 bitów w ciasteczku `HttpOnly`, `SameSite=Lax`, `Secure` i prefiks `__Host-` przy HTTPS; w bazie tylko skrót SHA-256; wygasanie po 30 dniach; zmiana hasła wylogowuje wszystkie urządzenia |
| CSRF | weryfikacja nagłówka `Origin` dla zapytań zmieniających stan oraz token CSRF w nagłówku `X-CSRF-Token` dla zapytań z sesją |
| Logowanie | stały czas odpowiedzi (także dla nieistniejących kont), blokada konta po 10 nieudanych próbach na 15 min, limit 10 prób/min na IP |
| Odzyskiwanie dostępu | 10 jednorazowych kodów odzyskiwania (80 bitów, w bazie tylko SHA-256), działa bez poczty; nieudane próby liczą się do blokady konta; słabe nowe hasło nie zużywa kodu |
| Weryfikacja dwuetapowa | TOTP (RFC 6238) z aplikacji w telefonie, każdy kod działa raz; drugi krok logowania przez jednorazowe wyzwanie (5 min, 5 prób); reset hasła nigdy jej nie omija; kody odzyskiwania jako zapas |
| Urządzenia | lista aktywnych sesji z opisem urządzenia, wylogowanie pojedynczego urządzenia, „Nie wylogowuj mnie” (bez niego sesja do zamknięcia przeglądarki, maks. 12 h); e-mail o zmianie hasła, adresu, kodów i 2FA |
| Linki z e-maili | jednorazowe, ważne 1 h (reset) lub 3 dni (weryfikacja), w bazie tylko skrót; reset nie zdradza, czy konto istnieje |
| Nagłówki | CSP bez `unsafe-inline`/`unsafe-eval`, `X-Frame-Options: DENY`, `frame-ancestors 'none'`, HSTS (przy HTTPS), `X-Content-Type-Options`, `Referrer-Policy: no-referrer`, COOP/CORP, `Permissions-Policy`, `Cache-Control: no-store` dla API |
| Wejście | walidacja Pydantic, limit rozmiaru zapytania (64 KB, także przy przesyłaniu porcjami), zapytania SQL parametryzowane, DOM budowany przez `textContent` (brak XSS z danych NFZ) |
| DoS | globalny limit zapytań na IP, limity slowapi, ograniczona liczba równoczesnych wyszukiwań (503 zamiast kolejki bez końca) i limit czasu (504), hashowanie haseł w osobnej puli wątków, limity subskrypcji i maili na adres, timeouty Caddy przeciw slowloris; szczegóły i wyniki testów w [docs/SECURITY_REVIEW.md](docs/SECURITY_REVIEW.md) |
| Dane | minimalizacja (współrzędne zaokrąglone, adresy w cache jako skrót), brak danych osobowych w logach, kontener bez uprawnień roota z systemem plików tylko do odczytu |
| Łańcuch dostaw | biblioteki frontendu hostowane lokalnie (bez CDN), `pip-audit` w CI, Dependabot dla Pythona i GitHub Actions |

## Zalecenia dla administratora instancji

- Uruchamiaj wyłącznie za HTTPS (np. profil `https` w `docker-compose.yml` z Caddy) i ustaw
  `TERMIN_PUBLIC_URL=https://…`, żeby włączyć `Secure`, `__Host-` i HSTS.
- **Nie wystawiaj portu 8000 do internetu**: uvicorn nie ma limitu czasu na nagłówki
  (slowloris). Ruch z zewnątrz tylko przez Caddy/nginx; compose domyślnie wiąże port z `127.0.0.1`.
- Ustaw `TERMIN_ALLOWED_HOSTS=["twoja.domena"]`.
- Przed atakami wolumetrycznymi (DDoS L3/L4) chroni tylko infrastruktura: ochrona DDoS
  dostawcy serwera albo CDN/WAF przed domeną.
- Nie wystawiaj `/metrics` publicznie (Caddyfile to blokuje).
- Ustaw `TERMIN_SECURITY_CONTACT` (trafia do `security.txt`).
- Rób kopie zapasowe bazy (`docs/DEPLOY.md`) i trzymaj je zaszyfrowane: zawierają adresy e-mail.
- Regularnie aktualizuj obraz (`docker compose pull && docker compose up -d --build`).
