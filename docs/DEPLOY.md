# Wdrożenie Termina

Termin to jeden proces Pythona (panel web + opcjonalnie obserwator powiadomień) i jeden plik
SQLite. Wystarczy najtańszy VPS (1 vCPU, 512 MB RAM).

## Konfiguracja

Wszystkie opcje to zmienne środowiskowe z prefiksem `TERMIN_` (pełna lista w
`termin/config.py`) albo plik `~/.config/termin/config.toml`. Najważniejsze:

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `TERMIN_PUBLIC_URL` | `http://localhost:8000` | Publiczny adres (linki „przestań obserwować”). |
| `TERMIN_DATA_DIR` | `~/.local/share/termin` (w Dockerze `/data`) | Katalog bazy SQLite. |
| `TERMIN_RATE_LIMIT` | `30/minute` | Limit „ciężkich” zapytań na adres IP. |
| `TERMIN_TELEGRAM_TOKEN` | brak | Token bota od @BotFather (włącza kanał Telegram). |
| `TERMIN_NTFY_SERVER` | `https://ntfy.sh` | Serwer ntfy (może być własny). |
| `TERMIN_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM` | brak | Powiadomienia e-mail. |
| `TERMIN_GEOCODER` | `auto` | `auto` (Nominatim + offline), `nominatim` albo `offline`. |
| `TERMIN_NOMINATIM_URL` | publiczny Nominatim | Własna instancja przy większym ruchu. |
| `TERMIN_OSRM_URL` | brak | Czas dojazdu (np. własny OSRM). |
| `TERMIN_TILE_URL` | kafelki OSM | Serwer kafelków mapy (przy ruchu > kilku tys. odsłon dziennie użyj własnego albo komercyjnego). |
| `TERMIN_WATCH_INTERVAL_MIN` | `60` | Co ile minut sprawdzać obserwacje. |
| `TERMIN_TRACKED_QUERIES` | `[]` | Zapytania do cyklicznych snapshotów, np. `["REZONANS@07"]`. |

| `TERMIN_OPERATOR_NAME`, `_ADDRESS`, `_EMAIL` | brak | **Wymagane**: dane usługodawcy w regulaminie i polityce prywatności. |
| `TERMIN_OPERATOR_ID`, `TERMIN_DPO_EMAIL`, `TERMIN_HOSTING_PROVIDER` | brak | NIP/KRS, inspektor ochrony danych, dostawca serwera (do polityki prywatności). |
| `TERMIN_SECURITY_CONTACT` | `operator_email` | Kontakt w `/.well-known/security.txt`. |
| `TERMIN_ACCOUNTS_ENABLED` | `true` | Konta użytkowników (opcjonalne dla użytkownika). |
| `TERMIN_SESSION_DAYS`, `TERMIN_MAX_FAILED_LOGINS`, `TERMIN_LOCKOUT_MINUTES` | 30, 10, 15 | Sesje i blokada logowania. |
| `TERMIN_ALLOWED_HOSTS` | `[]` | Dozwolone nagłówki `Host`, np. `["termin.example.org"]` (ochrona przed DNS rebinding). |
| `TERMIN_GLOBAL_REQUESTS_PER_MINUTE` | `300` | Łączny limit zapytań z jednego IP (poza `/healthz`). |
| `TERMIN_MAX_CONCURRENT_SEARCHES` | `8` | Równoczesne wyszukiwania; nadmiar dostaje 503 „spróbuj za chwilę”. |
| `TERMIN_SUBSCRIBE_RATE_LIMIT`, `TERMIN_MAX_SUBSCRIPTIONS_PER_TARGET` | `20/hour`, `10` | Limity tworzenia obserwacji. |
| `TERMIN_MAILS_PER_ADDRESS_PER_HOUR` | `3` | Maile (reset, weryfikacja) na jeden adres na godzinę. |
| `TERMIN_PUSH_ENABLED`, `TERMIN_VAPID_PRIVATE_KEY`, `TERMIN_VAPID_SUBJECT` | `true`, generowany, kontakt bezpieczeństwa | Powiadomienia Web Push. Bez klucza aplikacja generuje go i zapisuje w bazie (przy nowej bazie urządzenia trzeba włączyć ponownie; frontend robi to sam). |
| `TERMIN_BIND` (tylko compose) | `127.0.0.1` | Adres, na którym compose wystawia port 8000. Zostaw localhost. |

Szablon: `.env.example`.

## Zanim uruchomisz instancję publicznie (prawo i RODO)

Termin dostarcza szablony dokumentów i mechanizmy (zgody, eksport i usuwanie danych), ale
**to nie jest porada prawna**. Administrator instancji odpowiada za:

1. **Dane usługodawcy** w zmiennych `TERMIN_OPERATOR_*`. Strony `/regulamin`, `/prywatnosc`,
   `/kontakt` pokazują ostrzeżenie, dopóki ich brakuje.
2. **Przegląd dokumentów przez prawnika**: regulaminu (ustawa o świadczeniu usług drogą
   elektroniczną, art. 8), polityki prywatności (art. 13 RODO) i informacji o cookies
   (art. 399 Prawa komunikacji elektronicznej). Szablony są w `termin/web/legal_docs/`.
3. **Dane o zdrowiu (art. 9 RODO)**: informacja, jakiego świadczenia ktoś szuka, połączona z
   e-mailem lub czatem, może być daną o zdrowiu. Aplikacja zbiera wyraźną zgodę, ale
   administrator powinien:
   - prowadzić **rejestr czynności przetwarzania** (art. 30; zwolnienie dla małych podmiotów
     nie obejmuje danych szczególnych kategorii),
   - ocenić, czy potrzebna jest **ocena skutków dla ochrony danych (DPIA, art. 35)**,
   - rozważyć wyznaczenie **inspektora ochrony danych** (art. 37), jeśli przetwarzanie jest na dużą skalę.
4. **Umowy powierzenia** (art. 28 RODO) z dostawcą serwera i poczty e-mail.
5. **HTTPS** z prawidłowym certyfikatem (profil `https` w `docker-compose.yml`).
6. **Szyfrowane kopie zapasowe** bazy i procedurę zgłaszania naruszeń (72 h do UODO, art. 33).
7. Jeśli Termin jest prowadzony w ramach działalności gospodarczej: obowiązki informacyjne
   przedsiębiorcy (NIP/KRS w `TERMIN_OPERATOR_ID`).

## Instalacja na telefonie i komputerze

Termin to PWA: po otwarciu strony przez HTTPS przeglądarka pozwala ją zainstalować
(przycisk „Zainstaluj” w panelu albo menu przeglądarki; na iPhonie: Udostępnij → Do ekranu
początkowego). Nie trzeba publikować aplikacji w sklepach. Jeśli chcesz ją mieć w Google
Play, możesz opakować PWA w Trusted Web Activity (np. narzędziem Bubblewrap) bez zmian w kodzie.

## Docker Compose na VPS (zalecane)

```bash
git clone https://github.com/promilek2/pawel2jarzembowski-oss.git termin && cd termin
cp .env.example .env            # uzupełnij TERMIN_PUBLIC_URL itd.
docker compose up -d            # http://127.0.0.1:8000 na serwerze, start < 1 min
```

Port 8000 jest wystawiony tylko na `127.0.0.1`. **Nie zmieniaj tego na serwerze publicznym**:
uvicorn nie ma limitu czasu na nagłówki i padnie od ataku slowloris. Do internetu wystawiaj
aplikację przez Caddy (poniżej) albo nginx. Do testów w sieci lokalnej: `TERMIN_BIND=0.0.0.0`.

Z automatycznym HTTPS (Caddy + Let's Encrypt). Domena musi wskazywać na serwer, a porty 80 i 443
muszą być otwarte:

```bash
DOMAIN=termin.example.org docker compose --profile https up -d
```

Caddy nie zapisuje logów dostępowych i nie wystawia `/metrics` na zewnątrz. Ma też timeouty
na nagłówki i ciało zapytania (ochrona przed slowloris) oraz limit rozmiaru zapytania.

## Ochrona przed DDoS

Aplikacja broni się przed zalewem zapytań z jednego adresu i przed przeciążeniem (limity,
503 zamiast kolejki bez końca, timeouty proxy). Ataku wolumetrycznego z tysięcy adresów nie
zatrzyma żaden program na jednym serwerze, więc:

1. Włącz ochronę DDoS u dostawcy serwera (zwykle jest w cenie) albo postaw domenę za CDN/WAF
   (np. Cloudflare). Za CDN ustaw w Caddy `trusted_proxies` na jego zakresy adresów.
2. Firewall: otwarte tylko 80, 443 i SSH (najlepiej z ograniczeniem adresów).
3. Ustaw `TERMIN_ALLOWED_HOSTS` na swoją domenę.
4. Sprawdź odporność swojej instancji: `python scripts/load_test.py https://twoja.domena`
   (tylko na własnym serwerze). Model zagrożeń: [SECURITY_REVIEW.md](SECURITY_REVIEW.md).

Kontener działa jako użytkownik bez uprawnień (UID 10001) z systemem plików tylko do odczytu.
Ma healthcheck na `/healthz`. Dane leżą w wolumenie `termin-data`.

**Kopia zapasowa:**
```bash
docker compose exec termin python -c "import sqlite3; s=sqlite3.connect('/data/termin.db'); s.backup(sqlite3.connect('/data/backup.db'))"
docker compose cp termin:/data/backup.db ./termin-$(date +%F).db
```

**Aktualizacja:** `git pull && docker compose up -d --build`. Migracje bazy wykonują się same przy starcie.

**Za proxy z własnym CA** (sieci firmowe): `docker build --secret id=ca,src=/ścieżka/ca.crt .`

## Bez Dockera (systemd)

```bash
pipx install termin-nfz
sudo useradd --system --home /var/lib/termin --create-home termin
```

`/etc/systemd/system/termin.service`:

```ini
[Unit]
Description=Termin
After=network-online.target

[Service]
User=termin
Environment=TERMIN_DATA_DIR=/var/lib/termin
Environment=TERMIN_PUBLIC_URL=https://termin.example.org
EnvironmentFile=-/etc/termin.env
ExecStart=/usr/local/bin/termin serwer --host 127.0.0.1 --port 8000 --watcher
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/termin

[Install]
WantedBy=multi-user.target
```

Przed aplikacją postaw Caddy (skopiuj `deploy/Caddyfile`) albo nginx z timeoutami
(`client_header_timeout 10s; client_body_timeout 15s; client_max_body_size 64k;`).
Bot Telegram działa jako osobna usługa: `ExecStart=/usr/local/bin/termin telegram`.

## Fly.io

```bash
fly launch --no-deploy --name termin-twoja-nazwa   # wykryje Dockerfile
fly volumes create termin_data --size 1
```

W `fly.toml` dopisz:

```toml
[env]
  TERMIN_DATA_DIR = "/data"
  TERMIN_PUBLIC_URL = "https://termin-twoja-nazwa.fly.dev"

[mounts]
  source = "termin_data"
  destination = "/data"

[http_service]
  internal_port = 8000
  force_https = true
  auto_stop_machines = "off"   # obserwator musi działać w tle

[[http_service.checks]]
  path = "/healthz"
  interval = "30s"
  timeout = "5s"
```

Potem `fly secrets set TERMIN_TELEGRAM_TOKEN=...` i `fly deploy`. Jedna maszyna wystarczy.
SQLite nie lubi wielu replik, więc nie skaluj poziomo.

## Render (najprościej: jedno kliknięcie)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/promilek2/pawel2jarzembowski-oss/tree/claude/termin-nfz-setup-inoylh)

Przycisk czyta `render.yaml`: darmowy plan, Docker, health check `/healthz`, HTTPS i adres
`https://<nazwa>.onrender.com` (trafia sam do `TERMIN_PUBLIC_URL` przez `RENDER_EXTERNAL_URL`).
Po kilku minutach otwórz ten adres na telefonie i kliknij „Zainstaluj”.

**Poczta (opcjonalnie):** bez niej „Nie pamiętam hasła” działa przez kody odzyskiwania.
Żeby wysyłać linki resetu, potwierdzenia i powiadomienia, dodaj w Render → Environment
zmienne `TERMIN_SMTP_HOST`, `TERMIN_SMTP_PORT` (zwykle 587), `TERMIN_SMTP_USER`,
`TERMIN_SMTP_PASSWORD`, `TERMIN_SMTP_FROM`. Działa każdy serwer SMTP, np. darmowy plan
Brevo albo Gmail z hasłem do aplikacji (konto z weryfikacją dwuetapową → „Hasła do aplikacji”).

**Pomocnik AI (opcjonalnie):** okienko „?” działa od razu z wbudowaną pomocą. Żeby
odpowiadał model Claude, załóż konto na <https://console.anthropic.com>, doładuj kredyty
(Billing), utwórz klucz (API Keys → Create Key) i wklej go w Render → Environment jako
`TERMIN_AI_API_KEY`. Domyślny model to `claude-opus-5-5`; pytania o obsługę aplikacji są
proste, więc dużo taniej wyjdzie `TERMIN_AI_MODEL=claude-haiku-5-5`. `TERMIN_AI_DAILY_LIMIT`
(domyślnie 200 pytań dziennie na całą instancję) chroni rachunek; w konsoli Anthropic ustaw też
miesięczny limit wydatków. Na Render limity na IP są wspólne dla wszystkich, więc
`TERMIN_AI_RATE_LIMIT` (6/min, 40/dzień) dotyczy całej instancji. Przed publicznym
uruchomieniem zaakceptuj umowę powierzenia (DPA) Anthropic: polityka prywatności sama dopisze
Anthropic jako odbiorcę, gdy klucz jest ustawiony (ADR-0014).

Ograniczenia darmowego planu:

- usługa zasypia po 15 min bez ruchu, pierwsze wejście potem trwa około minuty,
- bez dysku: baza (historia, obserwacje, konta) znika przy restarcie i każdym wdrożeniu.
  Na stałe: plan płatny i w `render.yaml` odkomentuj sekcję `disk`,
- obserwator powiadomień nie działa niezawodnie, bo usługa śpi,
- aplikacja nie ufa nagłówkowi `X-Forwarded-For` (Render nie czyści go, więc klient mógłby
  podać dowolne IP), dlatego limity zapytań są wspólne dla wszystkich użytkowników instancji.
  Do użytku prywatnego to nie przeszkadza; publiczną instancję postaw na VPS z Caddy.

## Monitoring

- `/healthz`: 200, gdy baza odpowiada (nie odpytuje NFZ).
- `/metrics` (Prometheus): `termin_http_requests_total`, `termin_http_request_seconds`,
  `termin_nfz_requests_total{outcome}`, `termin_nfz_errors_total`, `termin_nfz_request_seconds`,
  `termin_cache_lookups_total{result}`, `termin_searches_total`, `termin_notifications_total`,
  `termin_rate_limited_total`. Nie wystawiaj `/metrics` publicznie (w Caddy jest zablokowane).
- Logi: JSON na stderr (`TERMIN_LOG_JSON=true`), bez danych osobowych.

## Dobre obyczaje wobec usług publicznych

- API NFZ: nie zwiększaj `TERMIN_NFZ_CONCURRENCY` (maks. 4) ani TTL cache w dół.
- Nominatim: przy ruchu większym niż kilka zapytań na minutę użyj własnej instancji
  albo `TERMIN_GEOCODER=offline` + tabeli kodów pocztowych (`scripts/build_postal_centroids.py`).
- Kafelki OSM: przestrzegaj [polityki kafelków](https://operations.osmfoundation.org/policies/tiles/).
