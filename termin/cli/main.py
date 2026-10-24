"""Komendy CLI: `termin ...`."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

from termin import __version__
from termin.config import get_settings
from termin.core.cache import SqliteCache
from termin.core.client import NfzClient, NfzError
from termin.logs import configure_logging
from termin.store.db import connect

app = typer.Typer(
    name="termin",
    help="Najszybszy termin u specjalisty na NFZ. Dane: API NFZ „Terminy leczenia”.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()
err_console = Console(stderr=True)


@app.callback()
def _setup(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Więcej logów diagnostycznych."),
) -> None:
    settings = get_settings()
    configure_logging("DEBUG" if verbose else settings.log_level, json=settings.log_json)


@app.command("wersja")
def version() -> None:
    """Pokaż wersję."""
    console.print(f"termin {__version__}")


@app.command("swiadczenia")
def benefits(fragment: str = typer.Argument(..., help="Fragment nazwy, np. 'okul'.")) -> None:
    """Sprawdź, jak NFZ nazywa świadczenia (słownik /benefits)."""

    async def run() -> list[str]:
        settings = get_settings()
        cache = SqliteCache(connect(settings.db_path))
        async with NfzClient(settings, cache=cache) as client:
            return await client.benefits(fragment)

    try:
        names = asyncio.run(run())
    except NfzError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    if not names:
        console.print("Brak świadczeń pasujących do tego fragmentu.")
    for name in names:
        console.print(name)


def main() -> None:
    app()
