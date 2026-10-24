"""Komendy CLI: `termin ...`."""

from __future__ import annotations

import asyncio
import sys
from contextlib import closing
from dataclasses import replace
from enum import StrEnum

import typer
from rich.console import Console

from termin import __version__
from termin.cli.render import print_json, print_results
from termin.config import Settings, get_settings
from termin.core.benefits import Resolution, Variant
from termin.core.cache import SqliteCache
from termin.core.client import NfzClient, NfzError
from termin.core.geo import GeocodingError
from termin.core.models import Case, SortMode
from termin.core.provinces import province_code
from termin.core.search import SearchError, SearchRequest, SearchResponse
from termin.logs import configure_logging
from termin.runtime import Runtime
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
    # W CLI logi są dla diagnostyki; zwykły użytkownik widzi tylko ostrzeżenia.
    configure_logging("DEBUG" if verbose else "WARNING", json=settings.log_json)


@app.command("wersja")
def version() -> None:
    """Pokaż wersję."""
    console.print(f"termin {__version__}")


@app.command("swiadczenia")
def benefits(fragment: str = typer.Argument(..., help="Fragment nazwy, np. 'okul'.")) -> None:
    """Sprawdź, jak NFZ nazywa świadczenia (słownik /benefits)."""

    async def run() -> list[str]:
        settings = get_settings()
        with closing(connect(settings.db_path)) as conn:
            async with NfzClient(settings, cache=SqliteCache(conn)) as client:
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


class Mode(StrEnum):
    najszybciej = "najszybciej"
    najblizej = "najblizej"
    kompromis = "kompromis"


def make_runtime(settings: Settings) -> Runtime:
    """Punkt wstrzyknięcia zależności (testy podmieniają transporty HTTP)."""
    return Runtime(settings)


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ask_variant(resolution: Resolution) -> Variant:
    label = resolution.entry.label if resolution.entry else resolution.query
    console.print(f"[bold]{label}[/bold]: szukasz dla dorosłych czy dla dzieci?")
    console.print("  1) dla dorosłych\n  2) dla dzieci")
    choice = typer.prompt("Wybierz", default="1")
    return Variant.CHILD if choice.strip() == "2" else Variant.ADULT


def _ask_candidate(resolution: Resolution) -> str:
    console.print(f"Nie jestem pewien, o co chodzi w „{resolution.text}”. Czy chodziło o:")
    for i, cand in enumerate(resolution.candidates, 1):
        console.print(f"  {i}) {cand.entry.label}")
    raw = typer.prompt("Wybierz numer", default="1")
    try:
        index = max(1, min(len(resolution.candidates), int(raw)))
    except ValueError:
        index = 1
    return resolution.candidates[index - 1].entry.aliases[0]


async def _run_search(runtime: Runtime, req: SearchRequest) -> SearchResponse:
    response = await runtime.search.search(req)
    res = response.resolution
    if res.status == "ambiguous":
        if not _is_interactive():
            options = "; ".join(c.entry.label for c in res.candidates)
            err_console.print(
                f"Nie jestem pewien, o co chodzi w „{res.text}”. Możliwe: {options}. "
                "Doprecyzuj zapytanie."
            )
            raise typer.Exit(3)
        text = _ask_candidate(res)
        response = await runtime.search.search(replace(req, text=text))
        res = response.resolution
    if res.status == "choose_variant":
        if _is_interactive():
            variant = _ask_variant(res)
        else:
            variant = Variant.ADULT
            err_console.print(
                "[dim]Pokazuję wyniki dla dorosłych. Dla dzieci dodaj --dla-dzieci.[/dim]"
            )
        response = await runtime.search.search(replace(req, text=res.text, variant=variant))
    return response


@app.command("szukaj")
def search(
    what: list[str] = typer.Argument(..., help="Czego szukasz, np. okulista, rezonans, zaćma."),
    where: str | None = typer.Option(
        None, "--gdzie", "-g", help="Miasto, kod pocztowy, adres albo 'lat,lon'."
    ),
    radius: float | None = typer.Option(None, "--promien", "-r", help="Promień w km."),
    province: str | None = typer.Option(
        None, "--wojewodztwo", "-w", help="Województwo: nazwa albo kod NFZ 01–16."
    ),
    mode: Mode = typer.Option(Mode.kompromis, "--tryb", "-t", help="Sposób sortowania."),
    urgent: bool = typer.Option(False, "--pilny", help="Przypadek pilny (skierowanie „pilne”)."),
    children: bool = typer.Option(False, "--dla-dzieci", help="Wariant dla dzieci."),
    adults: bool = typer.Option(False, "--dla-doroslych", help="Wariant dla dorosłych."),
    km_per_day: float | None = typer.Option(
        None,
        "--km-za-dzien",
        help="Kompromis: ile km dalej pojedziesz za każdy dzień szybciej (domyślnie 2).",
    ),
    travel_time: bool = typer.Option(
        False, "--czas-dojazdu", help="Licz czas dojazdu autem (wymaga TERMIN_OSRM_URL)."
    ),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=100, help="Ile wyników pokazać."),
    as_json: bool = typer.Option(False, "--json", help="Wynik jako JSON."),
) -> None:
    """Znajdź najszybszy termin u specjalisty na NFZ."""
    if children and adults:
        err_console.print("Wybierz --dla-dzieci albo --dla-doroslych, nie oba.")
        raise typer.Exit(2)
    code = None
    if province:
        code = province_code(province)
        if code is None:
            err_console.print(f"Nie znam województwa „{province}”.")
            raise typer.Exit(2)
    if where is None and code is None:
        err_console.print("Podaj, gdzie jesteś (--gdzie) albo województwo (--wojewodztwo).")
        raise typer.Exit(2)
    variant = Variant.CHILD if children else Variant.ADULT if adults else None
    req = SearchRequest(
        text=" ".join(what),
        where=where,
        radius_km=radius,
        province=code,
        case=Case.URGENT if urgent else Case.STABLE,
        variant=variant,
        mode=SortMode(mode.value),
        km_per_day=km_per_day,
        limit=limit,
        with_travel_time=travel_time,
    )
    settings = get_settings()

    async def run() -> SearchResponse:
        async with make_runtime(settings) as runtime:
            return await _run_search(runtime, req)

    try:
        response = asyncio.run(run())
    except (SearchError, GeocodingError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    except NfzError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(4) from exc
    if as_json:
        print_json(console, response)
    else:
        print_results(console, response)


def main() -> None:
    app()
