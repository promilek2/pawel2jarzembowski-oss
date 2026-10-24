from __future__ import annotations

import json
from pathlib import Path

import pytest
import structlog

from termin.config import Settings, get_settings
from termin.logs import REDACTED, configure_logging, get_logger, redact_pii


def test_defaults(tmp_path: Path) -> None:
    s = Settings()
    assert s.nfz_concurrency == 4
    assert s.nfz_base_url == "https://api.nfz.gov.pl/app-itl-api"
    assert s.db_path.name == "termin.db"


def test_toml_and_env_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text('km_per_day = 5.0\nntfy_server = "https://ntfy.example.org/"\n')
    monkeypatch.setenv("TERMIN_CONFIG", str(cfg))
    s = Settings()
    assert s.km_per_day == 5.0
    assert s.ntfy_server == "https://ntfy.example.org"
    monkeypatch.setenv("TERMIN_KM_PER_DAY", "7")
    assert Settings().km_per_day == 7.0


def test_concurrency_is_capped_at_four(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERMIN_NFZ_CONCURRENCY", "10")
    with pytest.raises(ValueError, match="nfz_concurrency"):
        Settings()


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_redact_pii() -> None:
    event = {"event": "x", "lat": 54.1, "email": "a@b.pl", "Target": "t", "benefit": "OKUL"}
    out = redact_pii(None, "info", event)
    assert out["lat"] == REDACTED
    assert out["email"] == REDACTED
    assert out["Target"] == REDACTED
    assert out["benefit"] == "OKUL"


def test_json_logs_have_no_pii(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json=True)
    get_logger("t").info("search", lat=54.5, lon=18.5, where="81-310 Gdynia", results=3)
    line = capsys.readouterr().err.strip().splitlines()[-1]
    data = json.loads(line)
    assert data["results"] == 3
    assert "54.5" not in line and "Gdynia" not in line
    structlog.reset_defaults()
