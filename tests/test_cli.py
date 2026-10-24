from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from termin.cli import commands as cli
from termin.config import Settings
from termin.runtime import Runtime, Transports
from tests.conftest import TODAY
from tests.fake_nfz import FakeNfz

runner = CliRunner()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeNfz:
    fake = FakeNfz.with_defaults()
    monkeypatch.setenv("TERMIN_GEOCODER", "offline")
    monkeypatch.setenv("COLUMNS", "200")

    def make(settings: Settings) -> Runtime:
        return Runtime(settings, transports=Transports(nfz=fake.transport()), today=lambda: TODAY)

    monkeypatch.setattr(cli, "make_runtime", make)
    return fake


def test_acceptance_cli_okulsta(fake: FakeNfz) -> None:
    result = runner.invoke(
        cli.app, ["szukaj", "okulsta", "--gdzie", "81-310 Gdynia", "--promien", "30"]
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "Okulista" in out
    assert "GDYNIA" in out
    assert "Dlaczego tu" in out
    assert "stan na" in out
    assert "potwierdź termin telefonicznie" in out
    assert "dla dorosłych" in out  # nieinteraktywnie: domyślnie dorośli + podpowiedź


def test_cli_json(fake: FakeNfz) -> None:
    result = runner.invoke(
        cli.app,
        ["szukaj", "okulista", "dla", "dzieci", "-g", "Gdynia", "-r", "100", "--json", "-n", "2"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "ok"
    assert data["variant"] == "dzieci"
    assert len(data["results"]) == 2
    first = data["results"][0]
    assert first["for_children"] is True
    assert {"first_date", "as_at", "phone", "explanation", "alerts"} <= first.keys()


def test_cli_fastest_mode_and_province(fake: FakeNfz) -> None:
    result = runner.invoke(
        cli.app,
        ["szukaj", "okulista", "--dla-doroslych", "-w", "pomorskie", "-t", "najszybciej"],
    )
    assert result.exit_code == 0, result.output
    assert "najszybciej" in result.output


def test_cli_validation_errors(fake: FakeNfz) -> None:
    assert runner.invoke(cli.app, ["szukaj", "okulista"]).exit_code == 2
    assert runner.invoke(cli.app, ["szukaj", "x", "-w", "Bawaria"]).exit_code == 2
    both = runner.invoke(
        cli.app, ["szukaj", "x", "-g", "Gdynia", "--dla-dzieci", "--dla-doroslych"]
    )
    assert both.exit_code == 2
    unknown = runner.invoke(cli.app, ["szukaj", "okulista", "-g", "Pcim Dolny 7"])
    assert unknown.exit_code == 2
    assert "Nie udało się znaleźć" in unknown.output


def test_cli_ambiguous_non_interactive(fake: FakeNfz) -> None:
    result = runner.invoke(cli.app, ["szukaj", "kolano", "-g", "Gdynia"])
    assert result.exit_code == 3
    assert "Endoproteza stawu kolanowego" in result.output


def test_cli_interactive_variant(fake: FakeNfz, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_is_interactive", lambda: True)
    result = runner.invoke(
        cli.app, ["szukaj", "okulista", "-g", "Gdynia", "-r", "100"], input="2\n"
    )
    assert result.exit_code == 0, result.output
    assert "DLA DZIECI" in result.output or "dla dzieci" in result.output


def test_cli_interactive_candidate(fake: FakeNfz, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_is_interactive", lambda: True)
    result = runner.invoke(cli.app, ["szukaj", "okul", "-g", "Gdynia"], input="1\n1\n")
    assert result.exit_code == 0, result.output


def test_cli_no_results(fake: FakeNfz) -> None:
    result = runner.invoke(cli.app, ["szukaj", "rezonans", "-g", "Gdynia"])
    assert result.exit_code == 0
    assert "Nie znaleziono terminów" in result.output


def test_cli_nfz_down(fake: FakeNfz, monkeypatch: pytest.MonkeyPatch) -> None:
    fake.fail_with = [503] * 20
    monkeypatch.setenv("TERMIN_NFZ_MAX_RETRIES", "1")
    result = runner.invoke(cli.app, ["szukaj", "rezonans", "-g", "Gdynia"])
    assert result.exit_code == 4
    assert "niedostępne" in result.output


def test_cli_benefits_and_version(fake: FakeNfz, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "termin 1.0.0" in runner.invoke(cli.app, ["wersja"]).output
    original = cli.NfzClient

    def patched(settings: Settings, **kw: object) -> object:
        return original(settings, transport=fake.transport())

    monkeypatch.setattr(cli, "NfzClient", patched)
    result = runner.invoke(cli.app, ["swiadczenia", "okul"])
    assert result.exit_code == 0
    assert "PORADNIA OKULISTYCZNA DLA DZIECI" in result.output
