"""Wspólne typy kanałów powiadomień."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class NotifyError(RuntimeError):
    """Nie udało się wysłać powiadomienia (kanał niedostępny albo źle skonfigurowany)."""


@dataclass(frozen=True, slots=True)
class Notification:
    title: str
    body: str
    """Treść w czystym tekście (bez HTML/Markdown)."""
    click_url: str | None = None
    """Link otwierany po kliknięciu (karta wyniku albo mapa)."""
    map_url: str | None = None
    unsubscribe_url: str | None = None
    tags: tuple[str, ...] = field(default=())


class Notifier(Protocol):
    channel: str

    async def send(self, target: str, notification: Notification) -> None:
        """Wysyła powiadomienie. Rzuca `NotifyError` przy niepowodzeniu."""
        ...
