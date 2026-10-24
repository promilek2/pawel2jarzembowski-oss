"""Treść powiadomienia o lepszym terminie."""

from __future__ import annotations

from termin.config import Settings
from termin.core.i18n import format_number, when
from termin.core.models import RankedResult
from termin.notify.base import Notification
from termin.store.subscriptions import Subscription


def map_url(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return None
    return (
        f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=17/{lat:.5f}/{lon:.5f}"
    )


def unsubscribe_url(settings: Settings, token: str) -> str:
    return f"{settings.public_url}/stop/{token}"


def build_notification(sub: Subscription, result: RankedResult, settings: Settings) -> Notification:
    entry, loc = result.entry, result.location
    first = entry.first_date
    assert first is not None and result.days_until is not None
    title = f"Wcześniejszy termin: {sub.label}, {first:%d.%m.%Y} ({when(result.days_until)})"
    address = ", ".join(x for x in (loc.address, loc.locality) if x)
    if result.distance_km is not None:
        address += f" ({format_number(result.distance_km)} km)"
    phone = loc.phone or entry.phone
    lines = [
        entry.provider or "Placówka",
        address,
        f"Tel.: {phone}" if phone else "Brak telefonu w danych NFZ",
    ]
    if entry.as_at:
        lines.append(f"Stan na {entry.as_at:%d.%m.%Y}. Zadzwoń, aby potwierdzić termin.")
    else:
        lines.append("Zadzwoń, aby potwierdzić termin.")
    the_map = map_url(loc.latitude, loc.longitude)
    if the_map:
        lines.append(f"Mapa: {the_map}")
    stop = unsubscribe_url(settings, sub.token)
    lines += ["", f"Przestań obserwować: {stop}", "Dane: NFZ. Termin nie jest aplikacją NFZ."]
    return Notification(
        title=title,
        body="\n".join(lines),
        click_url=f"{settings.public_url}/#wynik={entry.id}",
        map_url=the_map,
        unsubscribe_url=stop,
        tags=("hospital",),
    )
