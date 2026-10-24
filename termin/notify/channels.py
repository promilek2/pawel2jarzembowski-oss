"""Kanały powiadomień. Każdy dostaje wstrzykiwany transport HTTP (testy bez sieci)."""

from __future__ import annotations

import asyncio
import html
import shutil
import smtplib
from email.message import EmailMessage

import httpx

from termin import USER_AGENT
from termin.config import Settings
from termin.notify.base import Notification, Notifier, NotifyError


class NtfyNotifier:
    """ntfy.sh (albo własna instancja): bez konta, temat działa jak hasło."""

    channel = "ntfy"

    def __init__(self, server: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._server = server.rstrip("/")
        self._transport = transport

    async def send(self, target: str, notification: Notification) -> None:
        actions = []
        if notification.map_url:
            actions.append({"action": "view", "label": "Mapa", "url": notification.map_url})
        if notification.unsubscribe_url:
            actions.append(
                {
                    "action": "view",
                    "label": "Przestań obserwować",
                    "url": notification.unsubscribe_url,
                }
            )
        payload: dict[str, object] = {
            "topic": target,
            "title": notification.title,
            "message": notification.body,
            "tags": list(notification.tags),
            "actions": actions,
        }
        if notification.click_url:
            payload["click"] = notification.click_url
        async with httpx.AsyncClient(
            transport=self._transport, timeout=15, headers={"User-Agent": USER_AGENT}
        ) as http:
            try:
                response = await http.post(self._server, json=payload)
            except httpx.HTTPError as exc:
                raise NotifyError(f"ntfy: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise NotifyError(f"ntfy: HTTP {response.status_code}")


class TelegramNotifier:
    channel = "telegram"

    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._token = token
        self._transport = transport

    async def send(self, target: str, notification: Notification) -> None:
        lines = [f"<b>{html.escape(notification.title)}</b>", "", html.escape(notification.body)]
        await send_telegram_message(self._token, target, "\n".join(lines), self._transport)


async def send_telegram_message(
    token: str,
    chat_id: str,
    text: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(transport=transport, timeout=15) as http:
        try:
            response = await http.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        except httpx.HTTPError as exc:
            raise NotifyError(f"telegram: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise NotifyError(f"telegram: HTTP {response.status_code}")


class EmailNotifier:
    channel = "email"

    def __init__(self, settings: Settings) -> None:
        if not settings.smtp_host or not settings.smtp_from:
            raise NotifyError("e-mail: ustaw TERMIN_SMTP_HOST i TERMIN_SMTP_FROM")
        self._s = settings

    def _send_sync(self, target: str, notification: Notification) -> None:
        msg = EmailMessage()
        msg["Subject"] = notification.title
        msg["From"] = self._s.smtp_from
        msg["To"] = target
        msg.set_content(notification.body)
        host = self._s.smtp_host
        assert host is not None
        with smtplib.SMTP(host, self._s.smtp_port, timeout=20) as smtp:
            if self._s.smtp_starttls:
                smtp.starttls()
            if self._s.smtp_user and self._s.smtp_password:
                smtp.login(self._s.smtp_user, self._s.smtp_password)
            smtp.send_message(msg)

    async def send(self, target: str, notification: Notification) -> None:
        try:
            await asyncio.to_thread(self._send_sync, target, notification)
        except (OSError, smtplib.SMTPException) as exc:
            raise NotifyError(f"e-mail: {type(exc).__name__}") from exc


class DesktopNotifier:
    """notify-send (Linux) na komputerze, na którym działa watcher. Cel jest ignorowany."""

    channel = "desktop"

    async def send(self, target: str, notification: Notification) -> None:
        exe = shutil.which("notify-send")
        if exe is None:
            raise NotifyError("desktop: brak programu notify-send")
        proc = await asyncio.create_subprocess_exec(
            exe, "--app-name=Termin", notification.title, notification.body
        )
        if await proc.wait() != 0:
            raise NotifyError("desktop: notify-send zakończył się błędem")


def build_notifiers(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Notifier]:
    """Dostępne kanały według konfiguracji."""
    out: dict[str, Notifier] = {
        "ntfy": NtfyNotifier(settings.ntfy_server, transport),
        "desktop": DesktopNotifier(),
    }
    if settings.telegram_token:
        out["telegram"] = TelegramNotifier(settings.telegram_token, transport)
    if settings.smtp_host and settings.smtp_from:
        out["email"] = EmailNotifier(settings)
    return out
