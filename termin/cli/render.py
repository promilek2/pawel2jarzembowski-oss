"""Wyświetlanie wyników w terminalu (Rich) i jako JSON."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from termin.core.i18n import format_number, when
from termin.core.models import RankedResult
from termin.core.provinces import PROVINCES
from termin.core.ranking import alert_text
from termin.core.search import SearchResponse
from termin.core.text import plural_pl

NFZ_OFFICIAL = "https://terminyleczenia.nfz.gov.pl"


def _date_cell(r: RankedResult) -> Text:
    first = r.entry.first_date
    if first is None or r.days_until is None:
        return Text("brak terminu", style="yellow")
    label = f"{first:%d.%m.%Y}\n{when(r.days_until)}"
    return Text(label, style="bold green" if r.days_until >= 0 else "yellow")


def _distance_cell(r: RankedResult) -> str:
    parts = []
    if r.distance_km is not None:
        parts.append(f"{format_number(r.distance_km)} km")
    if r.travel_min is not None:
        parts.append(f"{r.travel_min:.0f} min")
    return "\n".join(parts) or "?"


def print_results(console: Console, response: SearchResponse) -> None:
    res = response.resolution
    where = ""
    if response.point is not None and response.point.label:
        where = f" · okolice: {response.point.label}"
    provinces = ", ".join(PROVINCES[c].name for c in response.provinces)
    console.print(
        f"[bold]{res.label}[/bold]{where} · województwa: {provinces} · "
        f"tryb: {response.request.mode.value}"
    )
    if response.benefit_names and len(response.benefit_names) > 1:
        console.print(f"[dim]Świadczenia w wynikach: {'; '.join(response.benefit_names)}[/dim]")

    if not response.results:
        console.print(
            "\n[yellow]Nie znaleziono terminów.[/yellow] Spróbuj zwiększyć promień "
            "(--promien), wybrać inne województwo albo sprawdzić nazwę świadczenia "
            "(termin swiadczenia <fragment>)."
        )
        return

    table = Table(show_lines=True, expand=True)
    table.add_column("#", justify="right", width=3)
    table.add_column("Termin", width=12)
    table.add_column("Placówka i adres", ratio=3)
    table.add_column("Odl.", justify="right", width=8)
    table.add_column("Telefon", width=14)
    for i, r in enumerate(response.results, 1):
        loc = r.location
        place = Text()
        place.append(r.entry.provider or "?", style="bold")
        if loc.place and loc.place != r.entry.benefit:
            place.append(f"\n{loc.place}")
        place.append(f"\n{loc.address or ''}, {loc.locality or ''}".strip(", "))
        if r.explanation:
            place.append(f"\nDlaczego tu: {r.explanation}", style="cyan")
        for alert in r.alerts:
            place.append(f"\n⚠ {alert_text(alert)}", style="yellow")
        if r.entry.as_at:
            place.append(f"\nstan na {r.entry.as_at:%d.%m.%Y}", style="dim")
        table.add_row(
            str(i), _date_cell(r), place, _distance_cell(r), loc.phone or r.entry.phone or "—"
        )
    console.print(table)
    extra = []
    if response.total_found > len(response.results):
        extra.append(f"pokazano {len(response.results)} z {response.total_found}")
    if response.without_location:
        n = response.without_location
        word = plural_pl(n, "placówkę", "placówki", "placówek")
        extra.append(f"pominięto {n} {word} bez współrzędnych")
    if extra:
        console.print(f"[dim]{'; '.join(extra)}[/dim]")
    console.print(
        "[dim]Dane: NFZ „Terminy leczenia” (aktualizowane przez placówki ok. raz w tygodniu). "
        "Zawsze potwierdź termin telefonicznie. To nie jest oficjalna aplikacja NFZ, oficjalna "
        f"wyszukiwarka: {NFZ_OFFICIAL}[/dim]"
    )


def result_to_dict(r: RankedResult) -> dict[str, Any]:
    loc = r.location
    return {
        "id": r.entry.id,
        "benefit": r.entry.benefit,
        "provider": r.entry.provider,
        "place": loc.place,
        "address": loc.address,
        "locality": loc.locality,
        "phone": loc.phone or r.entry.phone,
        "latitude": loc.latitude,
        "longitude": loc.longitude,
        "first_date": r.entry.first_date.isoformat() if r.entry.first_date else None,
        "as_at": r.entry.as_at.isoformat() if r.entry.as_at else None,
        "days_until": r.days_until,
        "distance_km": r.distance_km,
        "travel_min": r.travel_min,
        "score": r.score,
        "province": r.province,
        "awaiting": r.entry.awaiting,
        "average_period": r.entry.average_period,
        "for_children": r.entry.is_for_children,
        "many_places": r.entry.many_places,
        "accessibility": {
            "toilet": r.entry.toilet,
            "ramp": r.entry.ramp,
            "car_park": r.entry.car_park,
            "elevator": r.entry.elevator,
        },
        "explanation": r.explanation,
        "reasons": [{"code": x.code, "params": x.params} for x in r.reasons],
        "alerts": [{"code": a.code, "params": a.params, "text": alert_text(a)} for a in r.alerts],
    }


def response_to_dict(response: SearchResponse) -> dict[str, Any]:
    res = response.resolution
    point = response.point
    return {
        "status": res.status,
        "query": res.query,
        "label": res.label,
        "variant": res.variant.value,
        "candidates": [
            {"id": c.entry.id, "label": c.entry.label, "score": round(c.score)}
            for c in res.candidates
        ],
        "has_child_variant": bool(res.entry and res.entry.has_child_variant),
        "point": (
            {"lat": point.lat, "lon": point.lon, "label": point.label, "source": point.source}
            if point
            else None
        ),
        "provinces": response.provinces,
        "mode": response.request.mode.value,
        "case": int(response.request.case),
        "radius_km": response.request.radius_km,
        "today": response.today.isoformat(),
        "total_found": response.total_found,
        "without_location": response.without_location,
        "benefit_names": response.benefit_names,
        "medians": response.medians,
        "results": [result_to_dict(r) for r in response.results],
    }


def print_json(console: Console, response: SearchResponse) -> None:
    console.print_json(json.dumps(response_to_dict(response), ensure_ascii=False))
