from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from termin.cli import commands as cli
from termin.config import Settings
from termin.notify.channels import build_notifiers
from termin.runtime import Runtime, Transports
from tests.conftest import TODAY
from tests.fake_nfz import FakeNfz
from tests.fake_services import FakeNotifyServices

runner = CliRunner()


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> FakeNotifyServices:
    fake = FakeNfz.with_defaults()
    services = FakeNotifyServices()
    monkeypatch.setenv("TERMIN_GEOCODER", "offline")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(
        cli,
        "make_runtime",
        lambda s: Runtime(s, transports=Transports(nfz=fake.transport()), today=lambda: TODAY),
    )
    monkeypatch.setattr(cli, "make_notifiers", lambda s: build_notifiers(s, services.transport()))
    return services


def test_watch_flow(services: FakeNotifyServices) -> None:
    add = runner.invoke(
        cli.app,
        [
            "obserwuj",
            "dodaj",
            "okulista",
            "-k",
            "ntfy",
            "-c",
            "moj-temat",
            "-g",
            "Gdynia",
            "-r",
            "30",
            "--zgoda",
        ],
    )
    assert add.exit_code == 0, add.output
    token = next(line.split()[-1] for line in add.output.splitlines() if line.startswith("Token"))
    listing = runner.invoke(cli.app, ["obserwuj", "lista"])
    assert token in listing.output
    run = runner.invoke(cli.app, ["watcher", "--raz"])
    assert run.exit_code == 0, run.output
    assert "powiadomień: 1" in run.output
    assert services.ntfy[0]["topic"] == "moj-temat"
    again = runner.invoke(cli.app, ["watcher", "--raz"])
    assert "powiadomień: 0" in again.output
    assert runner.invoke(cli.app, ["obserwuj", "usun", token]).exit_code == 0
    assert runner.invoke(cli.app, ["obserwuj", "usun", token]).exit_code == 1
    assert "Brak obserwacji" in runner.invoke(cli.app, ["obserwuj", "lista"]).output


def test_watch_errors(services: FakeNotifyServices) -> None:
    bad = runner.invoke(
        cli.app,
        ["obserwuj", "dodaj", "okulista", "-k", "sms", "-c", "x", "-g", "Gdynia", "--zgoda"],
    )
    assert bad.exit_code == 2
    woj = runner.invoke(
        cli.app,
        ["obserwuj", "dodaj", "okulista", "-k", "ntfy", "-c", "x", "-w", "Bawaria", "--zgoda"],
    )
    assert woj.exit_code == 2
    ok = runner.invoke(
        cli.app,
        [
            "obserwuj",
            "dodaj",
            "rezonans",
            "-k",
            "ntfy",
            "-c",
            "x",
            "-w",
            "pomorskie",
            "--przed",
            "31.12.2026",
            "--pilny",
            "--zgoda",
        ],
    )
    assert ok.exit_code == 0, ok.output


def test_data_commands(services: FakeNotifyServices, tmp_path: Any) -> None:
    runner.invoke(
        cli.app,
        ["obserwuj", "dodaj", "okulista", "-k", "ntfy", "-c", "t", "-g", "Gdynia", "--zgoda"],
    )
    shown = runner.invoke(cli.app, ["dane", "pokaz"])
    assert "subscriptions: 1" in shown.output
    assert runner.invoke(cli.app, ["dane", "usun"], input="n\n").exit_code == 1
    wiped = runner.invoke(cli.app, ["dane", "usun", "--tak", "--z-historia"])
    assert wiped.exit_code == 0
    assert "subscriptions: 1" in wiped.output
    assert "subscriptions: 0" in runner.invoke(cli.app, ["dane", "pokaz"]).output


def test_watch_requires_consent(services: FakeNotifyServices) -> None:
    r = runner.invoke(
        cli.app, ["obserwuj", "dodaj", "okulista", "-k", "ntfy", "-c", "t", "-g", "Gdynia"]
    )
    assert r.exit_code == 2
    assert "zgody" in r.output


def test_telegram_requires_token(services: FakeNotifyServices) -> None:
    assert runner.invoke(cli.app, ["telegram"]).exit_code == 2


def test_settings_used(services: FakeNotifyServices) -> None:
    assert Settings().notify_cooldown_h == 12
