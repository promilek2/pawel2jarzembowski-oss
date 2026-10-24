"""Konfiguracja: zmienne środowiskowe TERMIN_* oraz ~/.config/termin/config.toml.

Kolejność (od najważniejszej): argumenty jawne → env → plik TOML → wartości domyślne.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
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


class Settings(BaseSettings):
    """Ustawienia aplikacji. Każde pole można ustawić przez env `TERMIN_<NAZWA>`."""

    model_config = SettingsConfigDict(
        env_prefix="TERMIN_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # --- NFZ ---
    nfz_base_url: str = "https://api.nfz.gov.pl/app-itl-api"
    nfz_api_version: str = "1.3"
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
    public_url: str = "http://localhost:8000"
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
    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    tile_attribution: str = "© OpenStreetMap contributors"

    # --- Logi ---
    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("nfz_base_url", "nominatim_url", "ntfy_server", "public_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

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
