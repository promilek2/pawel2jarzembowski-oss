"""Ochrona przed przeciążeniem i nadużyciami na poziomie aplikacji (docs/SECURITY_REVIEW.md).

To druga linia obrony. Ataki wolumetryczne (DDoS L3/L4) trzeba zatrzymać wcześniej:
u dostawcy serwera, w CDN/WAF albo w reverse proxy.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class IpRateLimiter:
    """Okno stałe (1 min) na adres IP, z limitem liczby śledzonych adresów (ochrona pamięci)."""

    def __init__(
        self, per_minute: int, max_ips: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.per_minute = per_minute
        self.max_ips = max_ips
        self._clock = clock
        self._windows: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def allow(self, ip: str) -> bool:
        now = self._clock()
        start, count = self._windows.pop(ip, (now, 0))
        if now - start >= 60:
            start, count = now, 0
        count += 1
        self._windows[ip] = (start, count)  # na koniec: najświeższe
        while len(self._windows) > self.max_ips:
            self._windows.popitem(last=False)  # wyrzucamy najdawniej widziane
        return count <= self.per_minute


class MailThrottle:
    """Maks. N e-maili na adresata na godzinę (ochrona przed „bombardowaniem” skrzynek)."""

    def __init__(self, per_hour: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.per_hour = per_hour
        self._clock = clock
        self._sent: dict[str, deque[float]] = {}

    def allow(self, address: str) -> bool:
        now = self._clock()
        times = self._sent.setdefault(address.lower(), deque())
        while times and now - times[0] > 3600:
            times.popleft()
        if len(times) >= self.per_hour:
            return False
        times.append(now)
        if len(self._sent) > 50_000:  # porządki w pamięci
            for key in [k for k, v in self._sent.items() if not v][:10_000]:
                del self._sent[key]
        return True


class BodyLimitMiddleware:
    """Odrzuca treść zapytania większą niż limit, także przesyłaną porcjami (bez Content-Length).

    Treść (maks. `max_bytes`) jest wczytywana do pamięci przed wywołaniem aplikacji, więc
    zbyt duże zapytanie dostaje 413, zanim aplikacja zacznie je przetwarzać.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        if length is not None and length.isdigit() and int(length) > self.max_bytes:
            await _reject(send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break  # rozłączenie klienta
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                await _reject(send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        buffered = b"".join(chunks)
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": buffered, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


async def _reject(send: Send) -> None:
    body = '{"detail":"Za duże zapytanie."}'.encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def client_ip(scope_or_request: Any) -> str:
    """Adres klienta (po przetworzeniu X-Forwarded-For przez zaufane proxy w uvicorn)."""
    client = getattr(scope_or_request, "client", None)
    return client.host if client else "unknown"
