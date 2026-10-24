"""Pobiera PRAWDZIWE odpowiedzi API NFZ do tests/fixtures/live/ (ADR-0007).

Uruchom z sieci, która ma dostęp do api.nfz.gov.pl:

    python scripts/capture_fixtures.py

Skrypt robi kilka żądań, sekwencyjnie, z przerwą 1 s, żeby nie obciążać API.
Po pobraniu uruchom `pytest tests/test_parser.py`: test kontraktowy przepuści
te pliki przez parser.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from termin import USER_AGENT

BASE = "https://api.nfz.gov.pl/app-itl-api"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "live"
COMMON = {"format": "json", "api-version": "1.3"}


def get(client: httpx.Client, path: str, params: dict[str, str] | None = None) -> Any:
    time.sleep(1.0)
    response = client.get(f"{BASE}{path}", params={**(params or {}), **COMMON})
    response.raise_for_status()
    return response.json()


def save(name: str, payload: Any) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"zapisano {name}")


def main() -> None:
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30) as client:
        searches = [
            ("queues_okulistyczna_11.json", {"benefit": "PORADNIA OKULISTYCZNA", "province": "11"}),
            ("queues_rezonans_07.json", {"benefit": "REZONANS", "province": "07"}),
            ("queues_kardiolog_12_pilny.json", {"benefit": "KARDIOLOG", "province": "12"}),
        ]
        many_id: str | None = None
        first_id: str | None = None
        for name, params in searches:
            case = "2" if "pilny" in name else "1"
            payload = get(client, "/queues", {**params, "case": case, "limit": "25", "page": "1"})
            save(name, payload)
            for item in payload.get("data") or []:
                first_id = first_id or item.get("id")
                if (item.get("attributes") or {}).get("many-places") == "Y":
                    many_id = many_id or item.get("id")

        if first_id:
            save("queue_single.json", get(client, f"/queues/{first_id}"))
        if many_id:
            save("many_places.json", get(client, f"/many-places/{many_id}"))
        else:
            print("uwaga: nie znaleziono placówki z many-places=Y w próbce")
        save("benefits_okul.json", get(client, "/benefits", {"name": "okul", "limit": "25"}))
        save(
            "localities_gdy.json",
            get(client, "/localities", {"name": "gdy", "province": "11", "limit": "25"}),
        )


if __name__ == "__main__":
    main()
