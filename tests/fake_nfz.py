"""Atrapa API NFZ dla testów (httpx.MockTransport). Nigdy nie wychodzi do sieci.

Zachowuje się jak prawdziwe API na tyle, na ile potrzebujemy: filtruje po
fragmencie nazwy świadczenia, województwie i przypadku, stronicuje po 25.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx

FIXTURES = Path(__file__).parent / "fixtures"
MANY_PLACES_ID = "00000010-0000-4000-8000-000000000010"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def okulistyka_items() -> list[dict[str, Any]]:
    p1 = load_fixture("queues_okulistyczna_11_p1.json")
    p2 = load_fixture("queues_okulistyczna_11_p2.json")
    return [*p1["data"], *p2["data"]]


class FakeNfz:
    def __init__(self) -> None:
        self.items: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.many_places: dict[str, Any] = {}
        self.benefit_names: list[str] = list(load_fixture("benefits_okul.json")["data"])
        self.localities: list[str] = list(load_fixture("localities_gdy.json")["data"])
        self.fail_with: list[int] = []
        self.calls: list[httpx.Request] = []

    # ---- konfiguracja

    @classmethod
    def with_defaults(cls) -> FakeNfz:
        fake = cls()
        fake.add_items("11", okulistyka_items())
        many = load_fixture("many_places.json")
        fake.many_places[MANY_PLACES_ID] = many
        return fake

    def add_items(self, province: str, items: list[dict[str, Any]]) -> None:
        self.items[province].extend(items)
        for item in items:
            name = item["attributes"].get("benefit")
            if name and name not in self.benefit_names:
                self.benefit_names.append(name)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ---- obsługa

    def requests_to(self, path: str) -> list[httpx.Request]:
        return [r for r in self.calls if r.url.path.endswith(path)]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail_with:
            status = self.fail_with.pop(0)
            headers = {"Retry-After": "0"} if status == 429 else {}
            return httpx.Response(status, json={"errors": []}, headers=headers)
        path = re.sub(r"^/app-itl-api(-pcus)?", "", request.url.path)
        params = dict(request.url.params)
        if path == "/queues":
            return self._queues(params)
        if path.startswith("/queues/"):
            return self._queue(path.rsplit("/", 1)[-1])
        if path.startswith("/many-places/"):
            payload = self.many_places.get(path.rsplit("/", 1)[-1])
            if payload is None:
                return httpx.Response(404, json={"errors": [{"error-reason": "not found"}]})
            return httpx.Response(200, json=payload)
        if path == "/benefits":
            return self._dictionary(self.benefit_names, params)
        if path == "/localities":
            return self._dictionary(self.localities, params)
        return httpx.Response(404, json={"errors": [{"error-reason": "unknown path"}]})

    def _queues(self, params: dict[str, str]) -> httpx.Response:
        province = params.get("province")
        if province and province not in {f"{i:02d}" for i in range(1, 17)}:
            return httpx.Response(400, json=load_fixture("error_400.json"))
        benefit = params.get("benefit", "").upper()
        case = int(params.get("case", "1"))
        page = int(params.get("page", "1"))
        limit = min(25, int(params.get("limit", "10")))
        pools = [self.items[province]] if province else list(self.items.values())
        matching = [
            item
            for pool in pools
            for item in pool
            if benefit in (item["attributes"].get("benefit") or "").upper()
            and int(item["attributes"].get("case") or 1) == case
        ]
        count = len(matching)
        last = max(1, -(-count // limit))
        chunk = matching[(page - 1) * limit : page * limit]
        q = "/app-itl-api/queues?benefit=" + quote_plus(benefit)
        return httpx.Response(
            200,
            json={
                "meta": {"count": count, "page": page, "limit": limit},
                "links": {
                    "first": f"{q}&page=1",
                    "prev": f"{q}&page={page - 1}" if page > 1 else None,
                    "self": f"{q}&page={page}",
                    "next": f"{q}&page={page + 1}" if page < last else None,
                    "last": f"{q}&page={last}",
                },
                "data": chunk,
            },
        )

    def _queue(self, queue_id: str) -> httpx.Response:
        for pool in self.items.values():
            for item in pool:
                if item["id"] == queue_id:
                    return httpx.Response(200, json={"meta": {}, "data": item})
        return httpx.Response(404, json={"errors": [{"error-reason": "not found"}]})

    def _dictionary(self, names: list[str], params: dict[str, str]) -> httpx.Response:
        needle = params.get("name", "").upper()
        found = [n for n in names if needle in n.upper()]
        page = int(params.get("page", "1"))
        limit = int(params.get("limit", "25"))
        chunk = found[(page - 1) * limit : page * limit]
        has_next = page * limit < len(found)
        return httpx.Response(
            200,
            json={
                "meta": {"count": len(found), "page": page, "limit": limit},
                "links": {"next": "next" if has_next else None},
                "data": chunk,
            },
        )
