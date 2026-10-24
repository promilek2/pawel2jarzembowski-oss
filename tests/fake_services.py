"""Atrapy usług powiadomień (ntfy, Telegram) na httpx.MockTransport."""

from __future__ import annotations

import json
from typing import Any

import httpx


class FakeNotifyServices:
    def __init__(self) -> None:
        self.ntfy: list[dict[str, Any]] = []
        self.telegram: list[dict[str, Any]] = []
        self.telegram_updates: list[dict[str, Any]] = []
        self.fail = False

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            return httpx.Response(500)
        host = request.url.host
        if host == "api.telegram.org":
            if request.url.path.endswith("/sendMessage"):
                self.telegram.append(json.loads(request.content))
                return httpx.Response(200, json={"ok": True, "result": {}})
            if request.url.path.endswith("/getUpdates"):
                updates, self.telegram_updates = self.telegram_updates, []
                return httpx.Response(200, json={"ok": True, "result": updates})
            return httpx.Response(404)
        # wszystko inne traktujemy jak serwer ntfy (POST JSON na adres serwera)
        self.ntfy.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "x"})
