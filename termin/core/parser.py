"""Parsowanie odpowiedzi API NFZ do modeli domenowych.

Zasada: jeden zły rekord nie psuje całej odpowiedzi. Tam, gdzie schemat jest
niepewny (`/many-places/{id}`), parser szuka obiektów z polami adresowymi zamiast
zakładać konkretny kształt (ADR-0001, ADR-0007).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import ValidationError

from termin.core.models import Location, Page, QueueEntry
from termin.logs import get_logger

log = get_logger(__name__)

_LOCATION_KEYS = frozenset({"latitude", "longitude", "address", "locality"})


class ApiFormatError(ValueError):
    """Odpowiedź API ma nieoczekiwany kształt."""


def _flatten_item(item: Any) -> dict[str, Any] | None:
    """{'id': X, 'attributes': {...}} → {'id': X, ...}. Obsługuje też płaskie obiekty."""
    if not isinstance(item, dict):
        return None
    attrs = item.get("attributes")
    if isinstance(attrs, dict):
        flat = dict(attrs)
        if item.get("id") is not None:
            flat["id"] = str(item["id"])
        return flat
    return dict(item)


def parse_queue_item(item: Any) -> QueueEntry | None:
    flat = _flatten_item(item)
    if flat is None or not flat.get("id"):
        log.warning("nfz.parse.skip", reason="brak id lub zły typ")
        return None
    try:
        return QueueEntry.model_validate(flat)
    except ValidationError as exc:
        log.warning("nfz.parse.skip", reason="walidacja", errors=exc.error_count())
        return None


def parse_queues(payload: Any) -> Page:
    """Strona z `/queues`."""
    if not isinstance(payload, dict):
        raise ApiFormatError("odpowiedź /queues nie jest obiektem JSON")
    data = payload.get("data")
    if data is None:
        data = []
    if not isinstance(data, list):
        raise ApiFormatError("pole 'data' w /queues nie jest listą")

    entries: list[QueueEntry] = []
    skipped = 0
    for raw in data:
        entry = parse_queue_item(raw)
        if entry is None:
            skipped += 1
        else:
            entries.append(entry)

    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
    count = meta.get("count") if meta else None
    return Page(
        count=count if isinstance(count, int) else None,
        entries=tuple(entries),
        has_next=bool(links.get("next")) if links else False,
        skipped=skipped,
    )


def parse_queue(payload: Any) -> QueueEntry | None:
    """Pojedyncza kolejka z `/queues/{id}`."""
    if not isinstance(payload, dict):
        raise ApiFormatError("odpowiedź /queues/{id} nie jest obiektem JSON")
    data = payload.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    return parse_queue_item(data) if data is not None else None


def _walk_locations(node: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    if depth > 6:
        return
    if isinstance(node, list):
        for child in node:
            yield from _walk_locations(child, depth + 1)
    elif isinstance(node, dict):
        flat = _flatten_item(node) or {}
        if len(_LOCATION_KEYS & flat.keys()) >= 2:
            yield flat
            return
        for key, child in node.items():
            if key in {"meta", "links"}:
                continue
            yield from _walk_locations(child, depth + 1)


def parse_many_places(payload: Any) -> tuple[Location, ...]:
    """Lokalizacje z `/many-places/{id}`. Kształt niepewny, więc szukamy rekurencyjnie."""
    if not isinstance(payload, dict):
        raise ApiFormatError("odpowiedź /many-places nie jest obiektem JSON")
    out: list[Location] = []
    for raw in _walk_locations(payload.get("data", payload)):
        try:
            out.append(Location.model_validate(raw))
        except ValidationError:
            continue
    return tuple(out)


def parse_string_dictionary(payload: Any) -> tuple[list[str], bool]:
    """`/benefits` i `/localities`: lista nazw + informacja, czy jest kolejna strona.

    Obsługuje zarówno listę stringów, jak i listę obiektów z polem `name`/`attributes`.
    """
    if not isinstance(payload, dict):
        raise ApiFormatError("odpowiedź słownika nie jest obiektem JSON")
    data = payload.get("data") or []
    if not isinstance(data, list):
        raise ApiFormatError("pole 'data' słownika nie jest listą")
    names: list[str] = []
    for raw in data:
        if isinstance(raw, str):
            name: Any = raw
        elif isinstance(raw, dict):
            flat = _flatten_item(raw) or {}
            name = flat.get("name") or flat.get("benefit") or flat.get("locality")
        else:
            name = None
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    links = payload.get("links") if isinstance(payload.get("links"), dict) else {}
    return names, bool(links.get("next")) if links else False


def parse_error(payload: Any) -> str | None:
    """Komunikat błędu z odpowiedzi NFZ (`errors[].error-reason`)."""
    if not isinstance(payload, dict):
        return None
    errors = payload.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        reason = errors[0].get("error-reason") or errors[0].get("detail")
        return str(reason) if reason else None
    return None
