"""Konfiguracja: zmienne środowiskowe TERMIN_* oraz ~/.config/termin/config.toml.

Kolejność (od najważniejszej): argumenty jawne → env → plik TOML → wartości domyślne.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "termin"


def _data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "termin"


def config_file() -> Path:
    """Ścieżka pliku konfiguracyjnego (można nadpisać przez TERMIN_CONFIG)."""
    override = os.environ.get("TERMIN_CONFIG")
    return Path(override) if override else _config_dir() / "config.toml"


def _secret_env(name: str) -> SecretStr | None:
    value = os.environ.get(name)
    return SecretStr(value) if value else None


class Settings(BaseSettings):
    """Ustawienia aplikacji. Każde pole można ustawić przez env `TERMIN_<NAZWA>`."""

    model_config = SettingsConfigDict(
        env_prefix="TERMIN_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # --- NFZ ---
    nfz_base_url: str = "https://apinfz.nfz.gov.pl/app-itl-api-pcus"
    """API „Terminy leczenia” 1.4 (od 10.07.2026, z PCUŚ). Stare 1.3: …/app-itl-api."""
    nfz_api_version: str | None = None
    """Parametr `api-version`; dla 1.4 go nie wysyłamy, dla starego adresu ustaw „1.3”."""
    nfz_fallback_url: str | None = "https://api.nfz.gov.pl/app-itl-api"
    """Zapasowe API 1.3, gdy 1.4 nie odpowiada (pusta wartość wyłącza)."""
    nfz_requests_per_10s: int = Field(default=9, ge=1, le=10)
    """API 1.4 przyjmuje najwyżej 10 zapytań na 10 sekund (potem HTTP 429)."""
    nfz_concurrency: int = Field(default=4, ge=1, le=4)
    nfz_timeout_s: float = 20.0
    nfz_max_retries: int = Field(default=4, ge=1, le=8)
    nfz_max_pages: int = Field(default=40, ge=1, le=200)
    cache_ttl_queues_s: int = 6 * 3600
    cache_ttl_dictionary_s: int = 7 * 24 * 3600

    # --- Geo ---
    geocoder: Literal["auto", "nominatim", "offline"] = "auto"
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    osrm_url: str | None = None
    """URL instancji OSRM (np. https://router.project-osrm.org). Brak = bez czasu dojazdu."""

    # --- Ranking ---
    default_radius_km: float = 50.0
    km_per_day: float = Field(default=2.0, gt=0)
    """Kompromis: ile km dalej jestem gotów pojechać za każdy dzień szybciej."""
    minutes_per_day: float = Field(default=3.0, gt=0)
    """Kompromis przy czasie dojazdu: ile minut dłużej za każdy dzień szybciej."""
    stale_after_days: int = 14

    # --- Magazyn ---
    data_dir: Path = Field(default_factory=_data_dir)
    database_url: Path | None = None
    """Ścieżka pliku SQLite. Domyślnie <data_dir>/termin.db."""
    snapshot_on_search: bool = True
    """Zapisuj snapshot wyników NFZ przy każdym wyszukiwaniu (historia i trendy)."""
    snapshot_retention_days: int = 730
    tracked_queries: list[str] = Field(default_factory=list)
    """Zapytania do cyklicznych snapshotów: "FRAGMENT@WOJ[@PRZYPADEK]", np. "REZONANS@07"."""

    # --- Powiadomienia ---
    public_url: str = Field(
        default_factory=lambda: os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:8000"
    )
    """Publiczny adres panelu. Na Render domyślnie RENDER_EXTERNAL_URL (render.yaml)."""
    ntfy_server: str = "https://ntfy.sh"
    telegram_token: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    watch_interval_min: int = Field(default=60, ge=5)
    notify_cooldown_h: float = Field(default=12.0, ge=0)

    # --- Web ---
    rate_limit: str = "30/minute"
    """Limit „ciężkich” zapytań (wyszukiwanie, statystyki, subskrypcje) na adres IP."""
    rate_limit_light: str = "120/minute"
    """Limit lekkich zapytań (podpowiedzi, historia, .ics) na adres IP."""
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    tile_attribution: str = "© OpenStreetMap contributors"

    # --- Konta i bezpieczeństwo ---
    accounts_enabled: bool = True
    """Konta są opcjonalne: wyszukiwanie i obserwowanie działają też bez logowania."""
    session_days: int = Field(default=30, ge=1, le=365)
    cookie_secure: bool | None = None
    """Flaga Secure ciasteczek. None = automatycznie (gdy public_url zaczyna się od https)."""
    login_rate_limit: str = "10/minute"
    max_failed_logins: int = Field(default=10, ge=3)
    lockout_minutes: int = Field(default=15, ge=1)
    unverified_account_days: int = Field(default=7, ge=1)
    """Po tylu dniach usuwamy konta z niepotwierdzonym adresem e-mail (gdy działa poczta)."""
    max_body_kb: int = Field(default=64, ge=4)

    # --- Ochrona przed przeciążeniem i nadużyciami (docs/SECURITY_REVIEW.md) ---
    global_requests_per_minute: int = Field(default=300, ge=10)
    """Łączny limit zapytań HTTP z jednego adresu IP (wszystkie ścieżki poza /healthz)."""
    max_tracked_ips: int = Field(default=100_000, ge=1000)
    max_concurrent_searches: int = Field(default=8, ge=1)
    """Ile wyszukiwań (zapytań do NFZ) obsługujemy naraz; reszta czeka albo dostaje 503."""
    search_queue_timeout_s: float = Field(default=3.0, gt=0)
    search_timeout_s: float = Field(default=60.0, gt=0)
    """API 1.4 przyjmuje 10 zapytań na 10 s, więc duży promień (kilka województw) trwa dłużej."""
    subscribe_rate_limit: str = "20/hour"
    max_subscriptions_per_target: int = Field(default=10, ge=1)
    max_subscriptions_per_user: int = Field(default=25, ge=1)
    max_subscriptions_total: int = Field(default=50_000, ge=1)
    mails_per_address_per_hour: int = Field(default=3, ge=1)
    nominatim_max_wait_s: float = Field(default=3.0, gt=0)
    """Maks. czas oczekiwania w kolejce do Nominatim; dłużej → geokoder offline."""
    allowed_hosts: list[str] = Field(default_factory=list)
    """Dozwolone nagłówki Host (ochrona przed DNS rebinding). Pusta lista = bez sprawdzania."""
    telegram_commands_per_minute: int = Field(default=20, ge=1)

    # --- Powiadomienia Web Push (ADR-0012) ---
    push_enabled: bool = True
    vapid_private_key: str | None = None
    """Klucz prywatny VAPID (base64url albo PEM). Brak = generujemy i zapisujemy w bazie."""
    vapid_subject: str | None = None
    """Kontakt dla usług push (`mailto:` albo https). Domyślnie TERMIN_SECURITY_CONTACT."""

    # --- Pomocnik „Jak to działa?” (ADR-0014) ---
    ai_enabled: bool = True
    """Okienko pomocnika. Bez klucza API odpowiada z wbudowanej bazy pytań (tryb FAQ)."""
    ai_api_key: SecretStr | None = Field(default_factory=lambda: _secret_env("ANTHROPIC_API_KEY"))
    """Klucz Anthropic API (console.anthropic.com). Też z ANTHROPIC_API_KEY."""
    ai_model: str = "claude-opus-5-5"
    """Model Claude. Tańsza opcja do prostych pytań: claude-haiku-5-5."""
    ai_daily_limit: int = Field(default=200, ge=0)
    """Ile pytań dziennie (cała instancja) wysyłamy do API; potem tryb FAQ. Chroni rachunek."""
    ai_rate_limit: str = "6/minute;40/day"
    """Limit pytań do pomocnika z jednego adresu IP."""
    ai_timeout_s: float = Field(default=30.0, gt=0)

    # --- Dane operatora (do regulaminu, polityki prywatności, security.txt) ---
    operator_name: str | None = None
    """Nazwa administratora danych / usługodawcy, np. „Jan Kowalski” albo nazwa firmy."""
    operator_address: str | None = None
    operator_email: str | None = None
    operator_id: str | None = None
    """Opcjonalnie NIP / KRS / REGON."""
    dpo_email: str | None = None
    """Inspektor ochrony danych, jeśli jest wyznaczony."""
    security_contact: str | None = None
    """Adres do zgłaszania podatności (domyślnie operator_email)."""
    legal_effective_date: str = "2026-10-25"
    hosting_provider: str | None = None
    """Dostawca serwera (podmiot przetwarzający), np. „Hetzner Online GmbH”."""

    # --- Logi ---
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("nfz_base_url", "nominatim_url", "ntfy_server", "public_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.public_url.startswith("https://")

    @property
    def mail_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    @property
    def db_path(self) -> Path:
        return self.database_url or self.data_dir / "termin.db"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls, toml_file=config_file()),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
