"""Komendy CLI: `termin ...`."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
from contextlib import closing
from dataclasses import replace
from enum import StrEnum
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from termin import __version__
from termin.cli.render import print_json, print_results
from termin.config import Settings, get_settings
from termin.core.benefits import BenefitCatalog, Resolution, Variant
from termin.core.cache import SqliteCache
from termin.core.client import NfzClient, NfzError
from termin.core.geo import GeocodingError
from termin.core.models import Case, SortMode
from termin.core.provinces import PROVINCES, province_code
from termin.core.search import SearchError, SearchRequest, SearchResponse
from termin.logs import configure_logging
from termin.notify.base import Notifier
from termin.notify.channels import build_notifiers
from termin.notify.message import unsubscribe_url
from termin.notify.subscribe import (
    SubscribeError,
    SubscribeRequest,
    create_subscription,
    parse_date,
)
from termin.notify.telegram_bot import TelegramBot
from termin.notify.watcher import Watcher, WatchReport
from termin.runtime import Runtime
from termin.store.db import connect
from termin.store.snapshots import SnapshotStore, WaitStats, to_csv, to_json
from termin.store.subscriptions import Subscription, SubscriptionStore, wipe_user_data

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


@app.command("historia")
def history(
    queue_id: str = typer.Argument(..., help="Identyfikator kolejki (pole 'id' z --json)."),
    days: int = typer.Option(180, "--dni", help="Ile dni wstecz."),
    as_json: bool = typer.Option(False, "--json", help="Wynik jako JSON."),
) -> None:
    """Historia pierwszego wolnego terminu w placówce i kierunek zmian kolejki."""
    settings = get_settings()
    with closing(connect(settings.db_path)) as conn:
        trend = SnapshotStore(conn).trend(queue_id, days)
    if as_json:
        console.print_json(json.dumps(trend.to_dict(), ensure_ascii=False))
        return
    if not trend.points:
        console.print(
            "Brak historii dla tej kolejki. Historia powstaje z kolejnych wyszukiwań "
            "i snapshotów (termin snapshot)."
        )
        return
    table = Table("Dzień", "Pierwszy termin", "Czekanie (dni)", "Oczekujący", "Stan na")
    for p in trend.points:
        table.add_row(
            f"{p.taken_on:%d.%m.%Y}",
            f"{p.first_date:%d.%m.%Y}" if p.first_date else "—",
            str(p.wait_days) if p.wait_days is not None else "—",
            str(p.awaiting) if p.awaiting is not None else "—",
            f"{p.as_at:%d.%m.%Y}" if p.as_at else "—",
        )
    console.print(table)
    console.print(TREND_TEXT[trend.direction].format(change=abs(trend.change_days or 0)))


TREND_TEXT = {
    "shorter": "Kolejka się skraca (o {change} dni w badanym okresie).",
    "longer": "Kolejka się wydłuża (o {change} dni w badanym okresie).",
    "stable": "Kolejka jest stabilna.",
    "unknown": "Za mało danych, żeby ocenić trend.",
}


@app.command("statystyki")
def stats(
    what: list[str] = typer.Argument(..., help="Świadczenie, np. okulista, rezonans."),
    province: str = typer.Option(..., "--wojewodztwo", "-w", help="Województwo: nazwa albo kod."),
    urgent: bool = typer.Option(False, "--pilny", help="Przypadek pilny."),
    children: bool | None = typer.Option(
        None, "--dla-dzieci/--dla-doroslych", help="Tylko wariant dla dzieci / dla dorosłych."
    ),
    as_json: bool = typer.Option(False, "--json", help="Wynik jako JSON."),
) -> None:
    """Mediana i percentyle czasu oczekiwania w województwie, z podziałem na powiaty."""
    code = province_code(province)
    if code is None:
        err_console.print(f"Nie znam województwa „{province}”.")
        raise typer.Exit(2)
    settings = get_settings()
    case_no = 2 if urgent else 1

    async def run() -> WaitStats:
        async with make_runtime(settings) as rt:
            resolution = rt.catalog.resolve(" ".join(what), Variant.ANY)
            query = resolution.query
            today = rt.today()
            latest = rt.snapshots.latest_snapshot_id(query, code, case_no)
            if latest is None or latest[1] < today:
                await rt.take_snapshot(query, code, case_no, source="search")
            return rt.snapshots.wait_stats(query, code, case_no, for_children=children)

    try:
        result = asyncio.run(run())
    except NfzError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(4) from exc
    if as_json:
        console.print_json(json.dumps(result.to_dict(), ensure_ascii=False))
        return
    console.print(
        f"[bold]{result.query}[/bold] · {PROVINCES[code].name} · "
        f"{'pilny' if urgent else 'stabilny'} · stan z {result.taken_on or '—'}"
    )
    if result.median is None:
        console.print("Brak placówek z podanym terminem.")
        return
    console.print(
        f"Placówek: {result.n} (z terminem: {result.with_date}). Czas oczekiwania w dniach: "
        f"mediana {result.median:g}, 25% placówek ≤ {result.p25:g}, 75% ≤ {result.p75:g}, "
        f"90% ≤ {result.p90:g}, min {result.minimum}, maks {result.maximum}."
    )
    table = Table("Powiat (TERYT)", "Miejscowości", "Placówek", "Mediana dni")
    for d in sorted(result.districts, key=lambda d: d.median_days):
        table.add_row(d.teryt, ", ".join(d.localities[:4]), str(d.n), f"{d.median_days:g}")
    console.print(table)


class ExportFormat(StrEnum):
    csv = "csv"
    json = "json"


@app.command("eksport")
def export(
    fmt: ExportFormat = typer.Option(ExportFormat.csv, "--format", "-f"),
    what: str | None = typer.Option(None, "--swiadczenie", "-s", help="Fragment świadczenia."),
    province: str | None = typer.Option(None, "--wojewodztwo", "-w"),
    since: str | None = typer.Option(None, "--od", help="Data od (RRRR-MM-DD)."),
    until: str | None = typer.Option(None, "--do", help="Data do (RRRR-MM-DD)."),
    output: Path | None = typer.Option(None, "--plik", "-o", help="Plik wyjściowy."),
) -> None:
    """Eksport zebranych snapshotów (CSV/JSON) dla dziennikarzy i badaczy."""
    code = province_code(province) if province else None
    try:
        since_d = dt.date.fromisoformat(since) if since else None
        until_d = dt.date.fromisoformat(until) if until else None
    except ValueError as exc:
        err_console.print("Daty podaj w formacie RRRR-MM-DD.")
        raise typer.Exit(2) from exc
    query = BenefitCatalog.load().resolve(what, Variant.ANY).query if what else None
    settings = get_settings()
    with closing(connect(settings.db_path)) as conn:
        rows = SnapshotStore(conn).export_rows(
            query=query, province=code, since=since_d, until=until_d
        )
    text = to_csv(rows) if fmt is ExportFormat.csv else to_json(rows)
    if output:
        output.write_text(text, encoding="utf-8")
        err_console.print(f"Zapisano {len(rows)} wierszy do {output}")
    else:
        sys.stdout.write(text)


@app.command("snapshot")
def snapshot(
    what: list[str] = typer.Argument(None, help="Świadczenie (puste = tracked_queries z configu)."),
    province: str | None = typer.Option(None, "--wojewodztwo", "-w"),
    urgent: bool = typer.Option(False, "--pilny"),
) -> None:
    """Zapisz snapshot kolejek (do cyklicznego uruchamiania, np. z crona)."""
    settings = get_settings()
    jobs: list[tuple[str, str, int]] = []
    if what:
        code = province_code(province or "")
        if code is None:
            err_console.print("Podaj województwo (--wojewodztwo).")
            raise typer.Exit(2)
        query = BenefitCatalog.load().resolve(" ".join(what), Variant.ANY).query
        jobs.append((query, code, 2 if urgent else 1))
    else:
        jobs = parse_tracked(settings.tracked_queries)
        if not jobs:
            err_console.print("Brak zapytań: podaj świadczenie albo ustaw tracked_queries.")
            raise typer.Exit(2)

    async def run() -> None:
        async with make_runtime(settings) as rt:
            for query, code, case_no in jobs:
                await rt.take_snapshot(query, code, case_no)
                console.print(f"snapshot: {query} · {PROVINCES[code].name} · przypadek {case_no}")

    try:
        asyncio.run(run())
    except NfzError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(4) from exc


def parse_tracked(items: list[str]) -> list[tuple[str, str, int]]:
    """'REZONANS@07' / 'PORADNIA OKULISTYCZNA@11@2' → (fragment, kod, przypadek)."""
    out = []
    for item in items:
        parts = [p.strip() for p in item.split("@")]
        code = province_code(parts[1]) if len(parts) > 1 else None
        if not parts[0] or code is None:
            continue
        case_no = 2 if len(parts) > 2 and parts[2] == "2" else 1
        out.append((parts[0].upper(), code, case_no))
    return out


watch_app = typer.Typer(help="Obserwowanie: powiadomienia o wcześniejszym terminie.")
app.add_typer(watch_app, name="obserwuj")
data_app = typer.Typer(help="Twoje dane: podgląd i usuwanie.")
app.add_typer(data_app, name="dane")


def make_notifiers(settings: Settings) -> dict[str, Notifier]:
    """Punkt wstrzyknięcia kanałów powiadomień (testy podmieniają transport)."""
    return build_notifiers(settings)


@watch_app.command("dodaj")
def watch_add(
    what: list[str] = typer.Argument(..., help="Czego szukasz."),
    channel: str = typer.Option(..., "--kanal", "-k", help="ntfy | telegram | email | desktop"),
    target: str = typer.Option(
        "", "--cel", "-c", help="Temat ntfy, ID czatu Telegram albo adres e-mail."
    ),
    where: str | None = typer.Option(None, "--gdzie", "-g"),
    radius: float | None = typer.Option(None, "--promien", "-r"),
    province: str | None = typer.Option(None, "--wojewodztwo", "-w"),
    before: str | None = typer.Option(None, "--przed", help="Tylko terminy przed tą datą."),
    urgent: bool = typer.Option(False, "--pilny"),
    children: bool = typer.Option(False, "--dla-dzieci"),
) -> None:
    """Dodaj obserwację: dostaniesz powiadomienie, gdy pojawi się wcześniejszy termin."""
    settings = get_settings()
    code = province_code(province) if province else None
    if province and code is None:
        err_console.print(f"Nie znam województwa „{province}”.")
        raise typer.Exit(2)

    async def run() -> Subscription:
        async with make_runtime(settings) as rt:
            return await create_subscription(
                rt,
                SubscribeRequest(
                    text=" ".join(what),
                    channel=channel,
                    target=target,
                    where=where,
                    province=code,
                    radius_km=radius,
                    before_date=parse_date(before) if before else None,
                    case=Case.URGENT if urgent else Case.STABLE,
                    variant=Variant.CHILD if children else None,
                ),
            )

    try:
        sub = asyncio.run(run())
    except SubscribeError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(f"Obserwuję: [bold]{sub.label}[/bold] · {sub.area_label}")
    console.print(f"Token: {sub.token}")
    console.print(f"Wyłączenie: termin obserwuj usun {sub.token}")
    console.print(f"albo w przeglądarce: {unsubscribe_url(settings, sub.token)}")


@watch_app.command("lista")
def watch_list() -> None:
    """Pokaż obserwacje zapisane w tej instancji."""
    settings = get_settings()
    with closing(connect(settings.db_path)) as conn:
        subs = SubscriptionStore(conn).active()
    if not subs:
        console.print("Brak obserwacji.")
        return
    table = Table("Token", "Co", "Obszar", "Kanał", "Ostatnio zgłoszony")
    for s in subs:
        last = f"{s.last_notified_date:%d.%m.%Y}" if s.last_notified_date else "—"
        table.add_row(s.token, s.label, s.area_label or "", s.channel, last)
    console.print(table)


@watch_app.command("usun")
def watch_remove(token: str = typer.Argument(..., help="Token obserwacji.")) -> None:
    """Usuń obserwację."""
    settings = get_settings()
    with closing(connect(settings.db_path)) as conn:
        removed = SubscriptionStore(conn).delete(token)
    if not removed:
        err_console.print("Nie ma takiej obserwacji.")
        raise typer.Exit(1)
    console.print("Usunięto.")


@app.command("watcher")
def watcher(
    once: bool = typer.Option(False, "--raz", help="Jeden przebieg i koniec (np. z crona)."),
) -> None:
    """Sprawdzaj obserwacje cyklicznie i wysyłaj powiadomienia."""
    settings = get_settings()

    async def run() -> WatchReport | None:
        async with make_runtime(settings) as rt:
            w = Watcher(rt, make_notifiers(settings))
            if once:
                return await w.run_once()
            await w.run_forever()
            return None

    report = asyncio.run(run())
    if report is not None:
        console.print(
            f"obserwacji: {report.subscriptions}, zapytań do NFZ: {report.queries}, "
            f"powiadomień: {report.notified}, bez poprawy: {report.not_better}, "
            f"cooldown: {report.cooldown}, brak pasujących: {report.no_match}"
        )
        for error in report.errors:
            err_console.print(f"[yellow]{error}[/yellow]")


@app.command("telegram")
def telegram() -> None:
    """Uruchom bota Telegram (wymaga TERMIN_TELEGRAM_TOKEN)."""
    settings = get_settings()
    if not settings.telegram_token:
        err_console.print("Ustaw TERMIN_TELEGRAM_TOKEN (token od @BotFather).")
        raise typer.Exit(2)
    token = settings.telegram_token

    async def run() -> None:
        async with make_runtime(settings) as rt:
            bot = TelegramBot(rt, token)
            try:
                await bot.run_forever()
            finally:
                await bot.aclose()

    asyncio.run(run())


@data_app.command("usun")
def data_delete(
    history: bool = typer.Option(
        False, "--z-historia", help="Usuń też snapshoty NFZ i cache (dane publiczne)."
    ),
    yes: bool = typer.Option(False, "--tak", help="Nie pytaj o potwierdzenie."),
) -> None:
    """Usuń wszystkie dane użytkowników: obserwacje i cache geokodowania."""
    if not yes and not typer.confirm("Usunąć wszystkie obserwacje i zapamiętane lokalizacje?"):
        raise typer.Exit(1)
    settings = get_settings()
    with closing(connect(settings.db_path)) as conn:
        counts = wipe_user_data(conn, include_history=history)
    console.print("Usunięto: " + ", ".join(f"{k}: {v}" for k, v in counts.items()))


@data_app.command("pokaz")
def data_show() -> None:
    """Pokaż, jakie dane przechowuje ta instancja (liczby wierszy)."""
    settings = get_settings()
    with closing(connect(settings.db_path)) as conn:
        for table in ("subscriptions", "geocode_cache", "snapshots", "snapshot_rows", "http_cache"):
            n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
            console.print(f"{table}: {n}")
    console.print(f"Plik bazy: {settings.db_path}")


def main() -> None:
    app()
