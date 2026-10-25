"""Kompresja gzip tylko dla odpowiedzi bez sekretów (pliki aplikacji, wyniki wyszukiwania).

Odpowiedzi z tokenami (np. `/api/auth/me` z tokenem CSRF) zostają nieskompresowane, żeby
nie otwierać drogi atakom typu BREACH. Za Caddy (`encode zstd gzip`) to podwójne zabezpieczenie
niczego nie psuje: Caddy nie kompresuje drugi raz.
"""

from __future__ import annotations

from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

COMPRESSIBLE_PREFIXES = ("/static/", "/api/search", "/api/stats", "/api/export.")
COMPRESSIBLE_PATHS = frozenset({"/", "/sw.js", "/manifest.webmanifest"})


def compressible(path: str) -> bool:
    return path in COMPRESSIBLE_PATHS or path.startswith(COMPRESSIBLE_PREFIXES)


class SelectiveGZipMiddleware:
    def __init__(self, app: ASGIApp, minimum_size: int = 800) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and compressible(scope["path"]):
            await self.gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)
