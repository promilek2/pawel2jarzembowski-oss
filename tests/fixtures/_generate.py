"""Generator ZREKONSTRUOWANYCH fixtures API NFZ (patrz ADR-0007 i ADR-0013).

Środowisko budowy nie ma dostępu do api.nfz.gov.pl, więc odpowiedzi są odtworzone.
Nazwy świadczeń są prawdziwe (stan 2026-10, tests/fixtures/nfz_benefit_names_2026-10.txt),
nazwy placówek i numery telefonów fikcyjne. Pozycje 1–30 mają dawny format 1.3
(pierwszy wolny termin `dates.date`), pozycje 31–38 format 1.4 (od 10.07.2026):
prognoza PCUŚ tekstem, zwykle bez współrzędnych, czasem z zamienionymi, kolejki w CeR.

Prawdziwe odpowiedzi: `python scripts/capture_fixtures.py` (zapisuje do tests/fixtures/live/).

Uruchomienie: python tests/fixtures/_generate.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).parent
BASE = "/app-itl-api"

META_COMMON = {
    "context": "https://api.nfz.gov.pl/app-itl-api/#/",
    "title": "Kolejki",
    "provider": "Narodowy Fundusz Zdrowia",
    "date-published": "2019-05-31T00:00:00+02:00",
    "date-modified": "2026-10-23T03:00:00+02:00",
    "language": "PL",
    "content-type": "application/json; charset=utf-8",
    "message": None,
}

OKULISTYKA = "ŚWIADCZENIA Z ZAKRESU OKULISTYKI"

# (miejscowość, lat, lon, teryt_place)
TOWNS = [
    ("GDYNIA", 54.5189, 18.5305, "2262011"),
    ("GDAŃSK", 54.3520, 18.6466, "2261011"),
    ("SOPOT", 54.4416, 18.5601, "2264011"),
    ("RUMIA", 54.5709, 18.3880, "2215061"),
    ("WEJHEROWO", 54.6059, 18.2356, "2215011"),
    ("REDA", 54.6050, 18.3488, "2215051"),
    ("PUCK", 54.7178, 18.4100, "2211011"),
    ("KARTUZY", 54.3337, 18.1972, "2205024"),
    ("PRUSZCZ GDAŃSKI", 54.2621, 18.6363, "2204021"),
    ("TCZEW", 54.0924, 18.7779, "2214011"),
    ("STAROGARD GDAŃSKI", 53.9659, 18.5304, "2213011"),
    ("KOŚCIERZYNA", 54.1222, 17.9817, "2206011"),
    ("LĘBORK", 54.5394, 17.7488, "2208011"),
    ("SŁUPSK", 54.4641, 17.0287, "2263011"),
    ("MALBORK", 54.0360, 19.0379, "2209011"),
]


def queue_item(
    idx: int,
    *,
    benefit: str = OKULISTYKA,
    town: int = 0,
    date: str | None = "2026-12-01",
    as_at: str | None = "2026-10-21",
    phone: str | None = "58 000 00 00",
    lat: Any = "town",
    lon: Any = "town",
    many_places: str = "N",
    children: str = "N",
    age_range: Any = None,
    statistics: Any = "default",
    case: int = 1,
    pcus: str | None = None,
    in_cer: str | None = None,
) -> dict[str, Any]:
    name, tlat, tlon, teryt = TOWNS[town]
    dates = (
        None
        if date is None and as_at is None
        else {"applicable": True, "date": date, "date-situation-as-at": as_at}
    )
    if pcus is not None and dates is not None:
        dates = {"applicable": True, "pcus": pcus, "date-situation-as-at": as_at}  # API 1.4
    if statistics == "default":
        statistics = {
            "provider-data": {
                "awaiting": 100 + idx * 37,
                "removed": 20 + idx,
                "average-period": 30 + idx * 5,
                "update": "2026-09",
            },
            "computed-data": None,
        }
    return {
        "type": "queues",
        "id": f"0000{idx:04d}-0000-4000-8000-00000000{idx:04d}",
        "attributes": {
            "case": case,
            "benefit": benefit,
            "many-places": many_places,
            "provider": f"PRZYCHODNIA PRZYKŁADOWA NR {idx} SP. Z O.O.",
            "provider-code": f"11{idx:08d}",
            "regon-provider": f"0000{idx:05d}",
            "nip-provider": f"000000{idx:04d}",
            "teryt-provider": teryt,
            "place": benefit,
            "address": f"UL. TESTOWA {idx}",
            "locality": name,
            "phone": phone if phone is None else (phone[:-2] + f"{idx:02d}" if phone else phone),
            "teryt-place": teryt,
            "registry-number": f"000000{idx:06d}",
            "id-resort-part-VII": f"{idx:03d}",
            "id-resort-part-VIII": "1610",
            "benefits-for-children": children,
            "covid-19": "N",
            "toilet": "Y" if idx % 2 else "N",
            "ramp": "Y" if idx % 3 else "N",
            "car-park": "Y" if idx % 4 else "N",
            "elevator": "Y" if idx % 5 else "N",
            "latitude": tlat + idx * 0.001 if lat == "town" else lat,
            "longitude": tlon + idx * 0.001 if lon == "town" else lon,
            "statistics": statistics,
            "dates": dates,
            "benefits-provided": None,
            "age-range": age_range,
            **({"queue-in-cer": in_cer} if in_cer is not None else {}),
        },
    }


def page(
    items: list[dict[str, Any]],
    *,
    count: int,
    page_no: int,
    limit: int,
    query: str,
) -> dict[str, Any]:
    last = max(1, -(-count // limit))
    q = f"{BASE}/queues?{query}&limit={limit}&format=json&api-version=1.3"
    return {
        "meta": {**META_COMMON, "count": count, "page": page_no, "limit": limit, "url": q},
        "links": {
            "first": f"{q}&page=1",
            "prev": f"{q}&page={page_no - 1}" if page_no > 1 else None,
            "self": f"{q}&page={page_no}",
            "next": f"{q}&page={page_no + 1}" if page_no < last else None,
            "last": f"{q}&page={last}",
        },
        "data": items,
    }


def okulistyka_pomorskie() -> list[dict[str, Any]]:
    """30 pozycji, w tym przypadki brzegowe."""
    items: list[dict[str, Any]] = []
    for i in range(1, 31):
        town = i % len(TOWNS)
        day = 1 + (i * 7) % 28
        month = 11 + (i % 6)
        year = 2026 + (month - 1) // 12
        month = (month - 1) % 12 + 1
        kwargs: dict[str, Any] = {"town": town, "date": f"{year}-{month:02d}-{day:02d}"}
        if i % 6 == 0:
            kwargs["children"] = "Y"  # od 07.2026: ta sama nazwa, flaga dla dzieci
            kwargs["age_range"] = {"from": 0, "to": 18}
        items.append(queue_item(i, **kwargs))

    # --- przypadki brzegowe (nadpisują wybrane pozycje) ---
    items[0] = queue_item(1, town=0, date="2026-11-03")  # Gdynia, szybko
    items[1] = queue_item(2, town=0, date=None, as_at="2026-10-20")  # brak terminu
    items[2] = queue_item(3, town=1, lat=0, lon=0)  # współrzędne 0
    items[3] = queue_item(4, town=2, lat=None, lon=None, date="2026-11-10")  # brak współrzędnych
    items[4] = queue_item(5, town=3, phone="")  # brak telefonu
    items[5] = queue_item(6, town=4, date="2026-10-01")  # termin w przeszłości
    items[6] = queue_item(7, town=5, as_at="2026-09-15", date="2026-11-05")  # stare dane
    items[7] = queue_item(8, town=1, statistics=None)  # brak statystyk
    items[8] = queue_item(
        9,
        town=1,
        date="2026-11-20",
        statistics={
            "provider-data": None,
            "computed-data": {"average-period": 41, "update": "2026-10"},
        },
    )
    items[9] = queue_item(
        10, town=13, many_places="Y", date="2026-11-04"
    )  # Słupsk, wiele lokalizacji
    items[10] = queue_item(11, town=0, date=None, as_at=None)  # dates: null
    items[11] = queue_item(12, town=0, lat="54,5200", lon="18,5400", date="2026-11-25")  # przecinki
    items[12] = queue_item(
        13,
        town=6,
        children="Y",
        age_range="0-18",
        date="2026-11-06",
    )
    items[13] = queue_item(14, town=2, lat=45.0, lon=2.0)  # współrzędne poza Polską

    # --- API 1.4: PCUŚ zamiast pierwszego terminu ---
    v14 = {"as_at": "2026-10-15", "lat": None, "lon": None}
    items += [
        queue_item(31, town=0, pcus="2 tyg.", **v14),  # Gdynia, bez współrzędnych
        queue_item(32, town=1, pcus="1 mies. 3 tyg.", **v14),  # Gdańsk
        queue_item(33, town=2, pcus="5 dni", as_at="2026-10-15", lat=18.5601, lon=54.4416),
        queue_item(34, town=0, pcus="3 mies.", children="Y", **v14),  # dla dzieci (flaga)
        queue_item(
            35,
            town=1,
            pcus="0 dni",
            statistics={
                "provider-data": {"awaiting": 0, "removed": 0, "average-period": 0},
                "computed-data": None,
            },
            **v14,
        ),  # pusta kolejka
        queue_item(36, town=0, date=None, as_at=None, in_cer="Y", statistics=None),  # CeR
        queue_item(37, town=3, pcus="11 mies.", in_cer="N", **v14),
        queue_item(
            38, town=0, benefit="ODDZIAŁ OKULISTYCZNY", pcus="1 tyg.", **v14
        ),  # szersze dopasowanie NFZ
    ]
    return items


def write(name: str, payload: dict[str, Any]) -> None:
    (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    items = okulistyka_pomorskie()
    query = "benefit=OKULISTYKI&province=11&case=1"
    total = len(items)
    write(
        "queues_okulistyczna_11_p1.json",
        page(items[:25], count=total, page_no=1, limit=25, query=query),
    )
    write(
        "queues_okulistyczna_11_p2.json",
        page(items[25:], count=total, page_no=2, limit=25, query=query),
    )
    write(
        "queues_empty.json",
        page([], count=0, page_no=1, limit=25, query="benefit=XYZ&province=11&case=1"),
    )
    write(
        "queue_single.json",
        {"meta": {**META_COMMON, "url": f"{BASE}/queues/{items[0]['id']}"}, "data": items[0]},
    )
    many_id = items[9]["id"]
    write(
        "many_places.json",
        {
            "meta": {
                **META_COMMON,
                "title": "Wiele miejsc",
                "url": f"{BASE}/many-places/{many_id}",
            },
            "data": [
                {
                    "type": "many-places",
                    "id": f"{many_id}-1",
                    "attributes": {
                        "place": "PORADNIA OKULISTYCZNA - FILIA",
                        "address": "UL. PRZYKŁADOWA 1",
                        "locality": "GDYNIA",
                        "phone": "58 000 11 11",
                        "latitude": 54.5100,
                        "longitude": 18.5200,
                    },
                },
                {
                    "type": "many-places",
                    "id": f"{many_id}-2",
                    "attributes": {
                        "place": "PORADNIA OKULISTYCZNA - FILIA 2",
                        "address": "UL. PRZYKŁADOWA 2",
                        "locality": "LĘBORK",
                        "phone": None,
                        "latitude": 0,
                        "longitude": 0,
                    },
                },
            ],
        },
    )
    write(
        "benefits_okul.json",
        {
            "meta": {
                **META_COMMON,
                "title": "Słownik świadczeń",
                "count": 3,
                "page": 1,
                "limit": 25,
            },
            "links": {"first": None, "prev": None, "self": None, "next": None, "last": None},
            "data": [
                "ŚWIADCZENIA Z ZAKRESU OKULISTYKI",
                "ODDZIAŁ OKULISTYCZNY",
                "ODDZIAŁ OKULISTYCZNY DLA DZIECI",
            ],
        },
    )
    write(
        "localities_gdy.json",
        {
            "meta": {
                **META_COMMON,
                "title": "Słownik miejscowości",
                "count": 2,
                "page": 1,
                "limit": 25,
            },
            "links": {"first": None, "prev": None, "self": None, "next": None, "last": None},
            "data": ["GDYNIA", "GDYNIA (MIASTO)"],
        },
    )
    write(
        "error_400.json",
        {
            "errors": [
                {"error-code": "400", "error-reason": "Nieprawidłowa wartość parametru province"}
            ]
        },
    )


if __name__ == "__main__":
    main()
