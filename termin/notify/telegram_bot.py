"""Bot Telegram: /szukaj, /obserwuj, /lista, /stop (long polling, bez webhooka).

Składnia (średnik oddziela świadczenie od miejsca; „ w ” też działa):
    /szukaj okulista; 81-310 Gdynia
    /obserwuj rezonans; Gdańsk; przed 31.12.2026; 40 km
    /lista
    /stop <token>   albo   /stop wszystko
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from typing import Any

import httpx

from termin.assistant import faq_answer
from termin.core.benefits import Variant
from termin.core.client import NfzError
from termin.core.i18n import format_number, when
from termin.core.ranking import shorter_nearby_text
from termin.core.search import SearchError, SearchRequest
from termin.logs import get_logger
from termin.notify.base import NotifyError
from termin.notify.channels import send_telegram_message
from termin.notify.subscribe import (
    SubscribeError,
    SubscribeRequest,
    create_subscription,
    parse_date,
)
from termin.runtime import Runtime
from termin.store.subscriptions import SubscriptionStore
from termin.web.protection import IpRateLimiter

log = get_logger(__name__)

HELP = (
    "Cześć! Szukam najszybszych terminów u specjalistów na NFZ.\n\n"
    "<b>/szukaj</b> okulista; 81-310 Gdynia\n"
    "<b>/obserwuj</b> rezonans; Gdańsk; przed 31.12.2026; 40 km; zgoda\n"
    "<b>/lista</b>: Twoje obserwowane wyszukiwania\n"
    "<b>/stop</b> token albo <b>/stop wszystko</b>\n"
    "Pytanie o obsługę? Napisz je zwykłym tekstem, np. „co znaczy prognoza NFZ”.\n\n"
    "Dane: NFZ (aktualizowane przez placówki ok. raz w tygodniu). Zawsze potwierdź termin "
    "telefonicznie. To nie jest oficjalna aplikacja NFZ."
)

CONSENT_PROMPT = (
    "Zanim zacznę obserwować: zapiszę ID tego czatu oraz to, jakiego świadczenia szukasz. "
    "Taka informacja może dotyczyć zdrowia, dlatego potrzebuję Twojej wyraźnej zgody "
    "(art. 9 ust. 2 lit. a RODO). Zgodę wycofasz w każdej chwili komendą /stop, "
    "a szczegóły są w polityce prywatności.\n\n"
    "Jeśli się zgadzasz, wyślij:\n/obserwuj {args}; zgoda"
)

BOT_RESULTS = 20
"""Ile wyników bierzemy do porównania („najkrótsza prognoza w okolicy”); pokazujemy 3."""

_RADIUS = re.compile(r"^(\d{1,3}(?:[.,]\d+)?)\s*km$", re.IGNORECASE)
_BEFORE = re.compile(r"^przed\s+(.+)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    what: str
    where: str | None
    before: str | None = None
    radius_km: float | None = None
    consent: bool = False


def parse_query(text: str) -> ParsedQuery:
    """'okulista; Gdynia; przed 31.12.2026; 30 km' albo 'okulista w Gdyni'."""
    parts = [p.strip() for p in text.split(";") if p.strip()]
    if len(parts) == 1 and " w " in parts[0]:
        head, _, tail = parts[0].rpartition(" w ")
        parts = [head.strip(), tail.strip()]
    if not parts:
        return ParsedQuery("", None)
    what, rest = parts[0], parts[1:]
    where: str | None = None
    before: str | None = None
    radius: float | None = None
    consent = False
    for part in rest:
        if part.lower() in {"zgoda", "zgadzam się", "zgadzam sie"}:
            consent = True
        elif m := _BEFORE.match(part):
            before = m.group(1)
        elif m := _RADIUS.match(part):
            radius = float(m.group(1).replace(",", "."))
        elif where is None:
            where = part
    return ParsedQuery(what, where, before, radius, consent)


class TelegramBot:
    def __init__(
        self,
        runtime: Runtime,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.runtime = runtime
        self.token = token
        self.transport = transport
        self.store = SubscriptionStore(runtime.conn)
        self.offset = 0
        self.limiter = IpRateLimiter(runtime.settings.telegram_commands_per_minute, 50_000)
        self._http = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}", transport=transport, timeout=40
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def reply(self, chat_id: str, text: str) -> None:
        try:
            await send_telegram_message(self.token, chat_id, text, self.transport)
        except NotifyError as exc:
            log.warning("telegram.reply_failed", reason=str(exc))

    async def poll_once(self, long_poll_s: int = 30) -> int:
        """Jedno wywołanie getUpdates. Zwraca liczbę obsłużonych wiadomości."""
        try:
            response = await self._http.get(
                "/getUpdates", params={"offset": str(self.offset), "timeout": str(long_poll_s)}
            )
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("telegram.poll_failed", reason=type(exc).__name__)
            return 0
        handled = 0
        for update in data.get("result", []):
            self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
            message = update.get("message") or {}
            text = message.get("text")
            chat = message.get("chat") or {}
            if isinstance(text, str) and chat.get("id") is not None:
                await self.handle(str(chat["id"]), text)
                handled += 1
        return handled

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            await self.poll_once()

    async def handle(self, chat_id: str, text: str) -> None:
        if not self.limiter.allow(chat_id):
            return  # zalew komend z jednego czatu: ignorujemy do końca minuty
        command, _, args = text.strip()[:500].partition(" ")
        command = command.split("@", 1)[0].lower()
        handlers = {
            "/start": self._help,
            "/pomoc": self._help,
            "/help": self._help,
            "/szukaj": self._search,
            "/obserwuj": self._watch,
            "/lista": self._list,
            "/stop": self._stop,
        }
        handler = handlers.get(command)
        if handler is None:
            if not text.lstrip().startswith("/"):
                await self._question(chat_id, text.strip()[:500])  # zwykłe pytanie
                return
            await self.reply(chat_id, "Nie znam tej komendy. Wpisz /pomoc.")
            return
        await handler(chat_id, args.strip())

    async def _help(self, chat_id: str, args: str) -> None:
        if args:
            await self._question(chat_id, args)
            return
        await self.reply(chat_id, HELP)

    async def _question(self, chat_id: str, question: str) -> None:
        """Pytanie zwykłym tekstem: odpowiedź z wbudowanej pomocy (bez wysyłania do AI)."""
        answer = faq_answer(question)
        if answer.topic is None:
            await self.reply(chat_id, HELP)
            return
        await self.reply(
            chat_id,
            f"{html.escape(answer.reply)}\n\n<i>Przyciski są w aplikacji internetowej. "
            "Komendy bota: /pomoc</i>",
        )

    async def _search(self, chat_id: str, args: str) -> None:
        q = parse_query(args)
        if not q.what or not q.where:
            await self.reply(chat_id, "Napisz np.: /szukaj okulista; 81-310 Gdynia")
            return
        req = SearchRequest(text=q.what, where=q.where, radius_km=q.radius_km, limit=BOT_RESULTS)
        try:
            resp = await self.runtime.search.search(req)
            if resp.resolution.status == "choose_variant":
                resp = await self.runtime.search.search(
                    SearchRequest(
                        text=q.what,
                        where=q.where,
                        radius_km=q.radius_km,
                        limit=BOT_RESULTS,
                        variant=Variant.ADULT,
                    )
                )
        except (SearchError, NfzError) as exc:
            await self.reply(chat_id, html.escape(str(exc)))
            return
        res = resp.resolution
        if res.status == "ambiguous":
            options = "\n".join(f"• {html.escape(c.entry.label)}" for c in res.candidates)
            await self.reply(chat_id, f"Doprecyzuj, o co chodzi:\n{options}")
            return
        if not resp.results:
            await self.reply(chat_id, "Nie znalazłem terminów. Spróbuj dodać np. „; 80 km”.")
            return
        lines = [f"<b>{html.escape(res.label)}</b>"]
        for i, r in enumerate(resp.results[:3], 1):
            first = r.expected_date
            if r.entry.is_forecast:
                date_text = f"ok. {html.escape(r.entry.pcus or '')} (prognoza NFZ)"
            elif first and r.days_until is not None:
                date_text = f"{first:%d.%m.%Y} ({when(r.days_until)})"
            elif r.entry.in_cer:
                date_text = "zapisy przez IKP (Centralna e-Rejestracja)"
            else:
                date_text = "brak terminu"
            dist = f" · {format_number(r.distance_km)} km" if r.distance_km is not None else ""
            phone = r.location.phone or r.entry.phone or "brak telefonu"
            lines.append(
                f"\n{i}. <b>{date_text}</b>{dist}\n{html.escape(r.entry.provider or '')}\n"
                f"{html.escape(r.location.address or '')}, {html.escape(r.location.locality or '')}"
                f"\nTel.: {html.escape(phone)}"
            )
            if r.entry.as_at:
                lines.append(f"stan na {r.entry.as_at:%d.%m.%Y}")
        tip = shorter_nearby_text(resp.results)
        if tip:
            lines.append(f"\n<b>{html.escape(tip)}</b>")
        lines.append("\nPotwierdź termin telefonicznie. Obserwuj: /obserwuj " + html.escape(args))
        await self.reply(chat_id, "\n".join(lines))

    async def _watch(self, chat_id: str, args: str) -> None:
        q = parse_query(args)
        if not q.what or not q.where:
            await self.reply(chat_id, "Napisz np.: /obserwuj okulista; Gdynia; przed 31.12.2026")
            return
        if not q.consent:
            await self.reply(chat_id, CONSENT_PROMPT.format(args=html.escape(args)))
            return
        try:
            before = parse_date(q.before) if q.before else None
            sub = await create_subscription(
                self.runtime,
                SubscribeRequest(
                    text=q.what,
                    where=q.where,
                    radius_km=q.radius_km,
                    before_date=before,
                    channel="telegram",
                    target=chat_id,
                    consent=True,
                ),
            )
        except SubscribeError as exc:
            await self.reply(chat_id, html.escape(str(exc)))
            return
        before_text = f", przed {sub.before_date:%d.%m.%Y}" if sub.before_date else ""
        await self.reply(
            chat_id,
            f"Obserwuję: <b>{html.escape(sub.label)}</b> ({html.escape(sub.area_label or '')}"
            f"{before_text}).\nNapiszę, gdy pojawi się wcześniejszy termin.\n"
            f"Wyłączenie: /stop {sub.token}",
        )

    async def _list(self, chat_id: str, _args: str) -> None:
        subs = self.store.for_target("telegram", chat_id)
        if not subs:
            await self.reply(chat_id, "Nic nie obserwujesz. Dodaj: /obserwuj okulista; Gdynia")
            return
        lines = ["Obserwujesz:"]
        for s in subs:
            last = (
                f", ostatnio zgłoszony {s.last_notified_date:%d.%m.%Y}"
                if s.last_notified_date
                else ""
            )
            lines.append(f"• {html.escape(s.label)} ({html.escape(s.area_label or '')}{last})")
            lines.append(f"  /stop {s.token}")
        await self.reply(chat_id, "\n".join(lines))

    async def _stop(self, chat_id: str, args: str) -> None:
        if args.lower() in {"wszystko", "all", "*"}:
            n = self.store.delete_for_target("telegram", chat_id)
            await self.reply(chat_id, f"Usunięto obserwacje: {n}. Twoje dane zostały skasowane.")
            return
        sub = self.store.get(args) if args else None
        if sub is None or sub.channel != "telegram" or sub.target != chat_id:
            await self.reply(chat_id, "Nie znalazłem takiej obserwacji. Zobacz /lista.")
            return
        self.store.delete(sub.token)
        await self.reply(chat_id, f"Przestałem obserwować: {html.escape(sub.label)}.")


def updates_payload(*messages: tuple[int, str, str]) -> dict[str, Any]:
    """Pomocnik do testów i dokumentacji: (update_id, chat_id, tekst) → odpowiedź getUpdates."""
    return {
        "ok": True,
        "result": [
            {"update_id": uid, "message": {"chat": {"id": chat}, "text": text}}
            for uid, chat, text in messages
        ],
    }
