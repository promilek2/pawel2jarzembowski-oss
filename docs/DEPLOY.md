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

Szablon: `.env.example`.

## Docker Compose na VPS (zalecane)

```bash
git clone https://github.com/promilek2/pawel2jarzembowski-oss.git termin && cd termin
cp .env.example .env            # uzupełnij TERMIN_PUBLIC_URL itd.
docker compose up -d            # http://SERWER:8000, start < 1 min
```

Z automatycznym HTTPS (Caddy + Let's Encrypt). Domena musi wskazywać na serwer, a porty 80 i 443
muszą być otwarte:

```bash
DOMAIN=termin.example.org docker compose --profile https up -d
```

Caddy nie zapisuje logów dostępowych i nie wystawia `/metrics` na zewnątrz.

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

Przed aplikacją postaw nginx albo Caddy (`reverse_proxy 127.0.0.1:8000`).
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

## Render

New → Web Service → repozytorium → środowisko **Docker**. Dodaj **Disk** zamontowany w `/data`
(1 GB), zmienną `TERMIN_PUBLIC_URL` i Health Check Path `/healthz`. Darmowy plan usypia
usługę, więc obserwator nie będzie działał niezawodnie. Do powiadomień wybierz plan płatny.

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
