"""Przypomnienie kalendarzowe (.ics): „Zadzwoń do placówki X”."""

from __future__ import annotations

import datetime as dt

from termin.core.models import QueueEntry


def _escape(text: str) -> str:
    out = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return out.replace("\n", "\\n")


def _fold(line: str) -> str:
    """Zawijanie linii do 75 oktetów (RFC 5545)."""
    raw = line.encode()
    if len(raw) <= 75:
        return line
    parts: list[str] = []
    current = b""
    for ch in line:
        b = ch.encode()
        if len(current) + len(b) > (75 if not parts else 74):
            parts.append(current.decode())
            current = b""
        current += b
    parts.append(current.decode())
    return "\r\n ".join(parts)


def call_reminder(
    entry: QueueEntry,
    day: dt.date,
    *,
    hour: int = 8,
    now: dt.datetime | None = None,
    url: str | None = None,
) -> str:
    """Wydarzenie 15 min o `hour` (czas lokalny „pływający”) z alarmem."""
    now = now or dt.datetime.now(dt.UTC)
    start = dt.datetime.combine(day, dt.time(hour, 0))
    end = start + dt.timedelta(minutes=15)
    provider = entry.provider or "placówka"
    phone = entry.phone or "brak numeru w danych NFZ"
    address = ", ".join(x for x in (entry.address, entry.locality) if x)
    lines_desc = [
        f"Świadczenie: {entry.benefit}",
        f"Telefon: {phone}",
        f"Adres: {address}",
    ]
    if entry.first_date:
        lines_desc.append(f"Pierwszy wolny termin wg NFZ: {entry.first_date:%d.%m.%Y}")
    if entry.as_at:
        lines_desc.append(f"Stan na: {entry.as_at:%d.%m.%Y}")
    lines_desc += [
        "Przygotuj: skierowanie (albo kod e-skierowania i PESEL), PESEL, dokumentację.",
        "Dane: NFZ „Terminy leczenia”. Termin nie jest aplikacją NFZ.",
    ]
    if url:
        lines_desc.append(url)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Termin//termin-nfz//PL",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:termin-{entry.id}-{day:%Y%m%d}@termin-nfz",
        f"DTSTAMP:{now.astimezone(dt.UTC):%Y%m%dT%H%M%SZ}",
        f"DTSTART:{start:%Y%m%dT%H%M%S}",
        f"DTEND:{end:%Y%m%dT%H%M%S}",
        f"SUMMARY:{_escape(f'Zadzwoń: {provider}')}",
        f"DESCRIPTION:{_escape(chr(10).join(lines_desc))}",
        f"LOCATION:{_escape(address)}",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_escape(f'Zadzwoń do: {provider} ({phone})')}",
        "TRIGGER:-PT0M",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
