# Przegląd bezpieczeństwa i odporności na DoS

Stan na 2026-10-24. Dokument opisuje, przed czym Termin się broni, jak, i co zostaje po stronie
administratora instancji. Zgłaszanie podatności: [SECURITY.md](../SECURITY.md).

## Model zagrożeń w skrócie

Termin to publiczny panel bez obowiązkowego logowania. Najcenniejsze zasoby:

1. **Dostępność** panelu i obserwatora powiadomień.
2. **Limit grzecznościowy wobec API NFZ i Nominatim** (zablokowanie naszego IP przez NFZ wyłącza
   usługę dla wszystkich).
3. **Dane użytkowników**: e-maile, cele powiadomień, informacja o szukanym świadczeniu (art. 9 RODO).
4. **Reputacja nadawcy poczty** (nadużycie formularzy do spamowania cudzych skrzynek).

## Ataki na dostępność (DoS/DDoS)

| Atak | Ochrona w aplikacji | Ryzyko szczątkowe / co zrobić |
|------|---------------------|-------------------------------|
| **Zalew HTTP z jednego IP** (L7) | Globalny limit `TERMIN_GLOBAL_REQUESTS_PER_MINUTE` (300/min/IP, `/healthz` wyłączone) przed całą logiką; osobne limity slowapi dla ciężkich (`TERMIN_RATE_LIMIT`, 30/min) i lekkich ścieżek; pamięć limitera ograniczona (`TERMIN_MAX_TRACKED_IPS`). Odrzucenie kosztuje mikrosekundy i nie dotyka bazy. | Brak. |
| **Rozproszony zalew L7** (botnet, wiele IP) | Semafor wyszukiwań (`TERMIN_MAX_CONCURRENT_SEARCHES`, 8): nadmiar dostaje szybkie 503 po `TERMIN_SEARCH_QUEUE_TIMEOUT_S`, a całe wyszukiwanie ma twardy limit czasu (504). Cache odpowiedzi NFZ, semafor 4 do NFZ. `/healthz` i statyczne pliki zostają szybkie. | Duży botnet wyczerpie przepustowość serwera. **Postaw CDN/WAF** (np. Cloudflare z regułami rate limiting / „Under attack”) albo ochronę dostawcy. |
| **Wolumetryczny DDoS** (L3/L4: SYN flood, UDP, amplifikacja) | Nie da się obronić w aplikacji. | **Ochrona DDoS dostawcy** (większość VPS ma ją w cenie), CDN przed domeną, firewall wpuszczający tylko 80/443 (a przy CDN tylko jego adresy). |
| **Slowloris / slow POST** (połączenia wysyłające nagłówki lub ciało bardzo powoli) | Caddy: `read_header 10s`, `read_body 15s`, `idle 2m`, `max_header_size 16KB`. Uvicorn: `limit_concurrency` (`--max-polaczen`, 512), krótki keep-alive, `backlog`, limit rozmiaru zdarzenia h11. Compose domyślnie wystawia port 8000 **tylko na localhost**. | Uvicorn sam nie ma limitu czasu na nagłówki (test poniżej), więc **nigdy nie wystawiaj portu 8000 do internetu**, zawsze przez Caddy/nginx. |
| **Duże ciało zapytania** | `BodyLimitMiddleware`: 413 przy nagłówku `Content-Length` powyżej `TERMIN_MAX_BODY_KB` (64 KB) i przy przesyłaniu porcjami (chunked) po przekroczeniu limitu; Caddy `request_body max_size 64KB`. | Brak. |
| **Kosztowne hashowanie haseł** (zalew `/api/auth/login`, rejestracji) | scrypt liczony w osobnej puli 4 wątków z kolejką maks. 64; nadmiar dostaje 503 zamiast blokować pętlę zdarzeń. Limity 10/min na IP, blokada konta. | Brak. |
| **Wyczerpanie limitu Nominatim** | Kolejka z maksymalnym czasem oczekiwania (`TERMIN_NOMINATIM_MAX_WAIT_S`); po przekroczeniu geokoder offline (lista miejscowości i kodów pocztowych). | Przy dużym ruchu własna instancja Nominatim (DEPLOY.md). |
| **Drogie zapytania do NFZ** (nieznane świadczenie → wyszukiwanie zastępcze) | Wyszukiwanie zastępcze pobiera maks. 4 strony; semafor 4, retry z backoffem, cache. | Brak. |
| **Zapchanie bazy subskrypcjami** | Limity: 20 nowych/h na IP (`TERMIN_SUBSCRIBE_RATE_LIMIT`), 10 na cel powiadomień, 25 na konto, 50 000 łącznie. | Brak. |
| **Spam z bota Telegram** | Limit poleceń na czat na minutę, przycinanie wiadomości do 500 znaków. | Brak. |
| **Wyczerpanie zasobów kontenera** | Compose: limit 512 MB RAM i 1 CPU, `ulimits nofile 8192`, system plików tylko do odczytu. | Brak. |

### Wyniki testów obciążeniowych

Narzędzie: `scripts/load_test.py` (uruchamiaj **tylko na własnej instancji**).

| Scenariusz | Wynik |
|------------|-------|
| Zalew: 3000 zapytań `/api/search`, 200 równolegle, jedno IP | 30 × 200, pozostałe 429; `/healthz` w trakcie 20/20 OK |
| Slowloris: 600 połączeń bezpośrednio na uvicorn | `/healthz` przestaje odpowiadać (503): **podatne bez proxy** |
| Slowloris: 600 połączeń przez Caddy (`deploy/Caddyfile`) | `/healthz` 20/20 OK, Caddy zamyka wszystkie wolne połączenia |

Testy automatyczne: `tests/test_protection.py` (limity, 413, 503/504, kolejki, flood bota).

## Ataki na aplikację

| Atak | Ochrona |
|------|---------|
| **XSS** | DOM budowany przez `textContent`; CSP `script-src 'self'` bez `unsafe-inline`/`unsafe-eval`; strony serwera przez `string.Template` z escapowaniem HTML. |
| **SQL injection** | Wyłącznie zapytania parametryzowane (`sqlite3` z `?`). Testy z wstrzyknięciami w `tests/test_protection.py`. |
| **CSRF** | Weryfikacja `Origin` dla metod zmieniających stan + token `X-CSRF-Token` dla sesji; `SameSite=Lax`. |
| **Clickjacking** | `X-Frame-Options: DENY`, `frame-ancestors 'none'`. |
| **Przejęcie sesji** | Token 256 bitów, `HttpOnly`, `Secure` + `__Host-` przy HTTPS, w bazie tylko SHA-256, wylogowanie wszędzie po zmianie hasła. |
| **Brute force / credential stuffing** | Limit prób na IP, blokada konta, stały czas odpowiedzi (brak enumeracji kont), odrzucanie popularnych haseł. |
| **Nadużycie maili** (reset, weryfikacja na cudzy adres) | Maks. `TERMIN_MAILS_PER_ADDRESS_PER_HOUR` (3) wiadomości na adres na godzinę, niezależnie od IP. |
| **Host header / DNS rebinding** | `TERMIN_ALLOWED_HOSTS` włącza `TrustedHostMiddleware`; linki w mailach budowane z `TERMIN_PUBLIC_URL`, nie z nagłówka `Host`. |
| **Fałszowanie IP przez `X-Forwarded-For`** | Uvicorn ufa nagłówkowi tylko od adresów z `FORWARDED_ALLOW_IPS` (sieci prywatne Dockera). |
| **SSRF** | Serwer łączy się tylko z adresami z konfiguracji (NFZ, Nominatim, OSRM, ntfy, Telegram); użytkownik podaje jedynie nazwę tematu ntfy, nie URL. |
| **Wyciek danych w logach** | `redact_pii` w structlog, `SENSITIVE_KEYS`; Caddy bez logów dostępowych. |
| **Łańcuch dostaw** | Biblioteki frontendu lokalnie (bez CDN), `pip-audit` w CI, Dependabot. |
| **Ujawnienie metryk / wersji** | `/metrics` blokowane w Caddy; uvicorn bez nagłówka `Server`. |

## Lista kontrolna administratora

1. Wystaw aplikację **tylko przez reverse proxy** (`docker compose --profile https up -d`);
   port 8000 zostaje na `127.0.0.1` (`TERMIN_BIND`).
2. Ustaw `TERMIN_PUBLIC_URL=https://…` i `TERMIN_ALLOWED_HOSTS=["twoja.domena"]`.
3. Firewall: otwarte tylko 22 (najlepiej z ograniczeniem adresów), 80 i 443.
4. Włącz ochronę DDoS dostawcy albo postaw domenę za CDN/WAF. Za CDN ustaw w Caddy
   `trusted_proxies` na jego zakresy, żeby limity widziały prawdziwe IP klientów.
5. Monitoruj `termin_rate_limited_total` i czasy odpowiedzi w `/metrics`.
6. Aktualizuj obraz i zależności (Dependabot, `pip-audit`).
