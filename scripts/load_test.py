"""Prosty test obciążeniowy i odporności (tylko na WŁASNEJ instancji!).

    python scripts/load_test.py http://127.0.0.1:8000 --tryb zalew --zapytan 2000 --rownolegle 200
    python scripts/load_test.py http://127.0.0.1:8000 --tryb slowloris --polaczen 300

Tryby:
  zalew      wiele zapytań /api/search z jednego adresu (sprawdza limity i dostępność /healthz)
  slowloris  wiele połączeń wysyłających nagłówki bardzo powoli (sprawdza timeouty proxy)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import statistics
import time
from collections import Counter
from urllib.parse import urlsplit

import httpx


async def flood(base: str, total: int, parallel: int) -> None:
    codes: Counter[str] = Counter()
    latencies: list[float] = []
    health: list[float] = []
    sem = asyncio.Semaphore(parallel)
    limits = httpx.Limits(max_connections=parallel, max_keepalive_connections=parallel)
    async with httpx.AsyncClient(base_url=base, timeout=30, limits=limits) as client:

        async def one(i: int) -> None:
            async with sem:
                started = time.perf_counter()
                try:
                    r = await client.get(
                        "/api/search", params={"q": f"okulista {i % 7}", "where": "Gdynia"}
                    )
                    codes[str(r.status_code)] += 1
                except httpx.HTTPError as exc:
                    codes[type(exc).__name__] += 1
                latencies.append(time.perf_counter() - started)

        async def probe() -> None:
            async with httpx.AsyncClient(base_url=base, timeout=10) as hc:
                for _ in range(20):
                    started = time.perf_counter()
                    try:
                        r = await hc.get("/healthz")
                        health.append(time.perf_counter() - started if r.status_code == 200 else -1)
                    except httpx.HTTPError:
                        health.append(-1)
                    await asyncio.sleep(0.2)

        started = time.perf_counter()
        await asyncio.gather(probe(), *(one(i) for i in range(total)))
        elapsed = time.perf_counter() - started
    lat = sorted(latencies)
    ok_health = [h for h in health if h >= 0]
    print(f"zapytań: {total} w {elapsed:.1f} s ({total / elapsed:.0f}/s)")
    print("kody odpowiedzi:", dict(codes))
    p50 = statistics.median(lat) * 1000
    p95 = lat[int(len(lat) * 0.95)] * 1000
    print(f"czas odpowiedzi p50={p50:.0f} ms p95={p95:.0f} ms")
    worst = max(ok_health, default=0) * 1000
    print(f"/healthz w trakcie: {len(ok_health)}/{len(health)} OK, maks. {worst:.0f} ms")


async def slowloris(base: str, connections: int, seconds: int) -> None:
    parts = urlsplit(base)
    host, port = parts.hostname or "127.0.0.1", parts.port or 80
    writers = []
    for _ in range(connections):
        try:
            _, w = await asyncio.open_connection(host, port)
            w.write(b"GET / HTTP/1.1\r\nHost: x\r\n")
            await w.drain()
            writers.append(w)
        except OSError:
            break
    print(f"otwarte powolne połączenia: {len(writers)}")
    deadline = time.monotonic() + seconds
    results: Counter[str] = Counter()
    async with httpx.AsyncClient(base_url=base, timeout=5) as hc:
        while time.monotonic() < deadline:
            for w in writers:
                if w.is_closing() or w.transport.get_extra_info("socket") is None:
                    continue
                with contextlib.suppress(OSError):
                    w.write(b"X-a: b\r\n")  # nagłówek co sekundę, nigdy nie kończymy
            try:
                r = await hc.get("/healthz")
                results[str(r.status_code)] += 1
            except httpx.HTTPError as exc:
                results[type(exc).__name__] += 1
            await asyncio.sleep(1)
    alive = 0
    for w in writers:
        try:
            w.write(b"X-a: b\r\n")
            await asyncio.wait_for(w.drain(), timeout=1)
            alive += 0 if w.is_closing() else 1
        except (OSError, TimeoutError):
            pass
    print("/healthz w trakcie ataku:", dict(results))
    print(f"powolnych połączeń wciąż otwartych po {seconds} s: {alive}")
    for w in writers:
        w.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--tryb", choices=["zalew", "slowloris"], default="zalew")
    p.add_argument("--zapytan", type=int, default=1000)
    p.add_argument("--rownolegle", type=int, default=100)
    p.add_argument("--polaczen", type=int, default=300)
    p.add_argument("--sekund", type=int, default=15)
    a = p.parse_args()
    if a.tryb == "zalew":
        asyncio.run(flood(a.url.rstrip("/"), a.zapytan, a.rownolegle))
    else:
        asyncio.run(slowloris(a.url.rstrip("/"), a.polaczen, a.sekund))


if __name__ == "__main__":
    main()
