"""Generuje ikony PNG aplikacji (bez zależności graficznych): bilet z ptaszkiem (jak icon.svg).

Użycie: python scripts/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "termin" / "web" / "static" / "icons"
INK = (18, 24, 43, 255)
WHITE = (255, 255, 255, 255)
PERF = (203, 208, 218, 255)
SIGNAL = (255, 106, 26, 255)
CLEAR = (0, 0, 0, 0)


def png(width: int, pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, width, 8, 6, 0, 0, 0)  # RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def inside_round_rect(
    x: float, y: float, x0: float, y0: float, x1: float, y1: float, r: float
) -> bool:
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def dist_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5


def inside_ticket(x: float, y: float) -> bool:
    """Bilet 44×32 z półkolistymi wcięciami po bokach (jak ścieżka w icon.svg)."""
    if not (10 <= x <= 54 and 16 <= y <= 48):
        return False
    return all((x - cx) ** 2 + (y - 32) ** 2 >= 25 for cx in (10, 54))


def draw(size: int, *, maskable: bool) -> bytes:
    # Układ w jednostkach 64×64 (jak icon.svg). Wariant maskable ma pełne tło i
    # treść zmniejszoną do strefy bezpiecznej.
    scale = size / 64
    shrink = 0.78 if maskable else 1.0
    offset = 32 * (1 - shrink)
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            u = (px + 0.5) / scale
            v = (py + 0.5) / scale
            inside = maskable or inside_round_rect(u, v, 0, 0, 64, 64, 14)
            color = INK if inside else CLEAR
            x = (u - offset) / shrink
            y = (v - offset) / shrink
            if inside_ticket(x, y):
                color = WHITE
                if 23 <= x <= 25 and 18 <= y <= 46 and int((y - 18) // 3) % 2 == 0:
                    color = PERF
            check = min(
                dist_to_segment(x, y, 31, 32, 36, 37), dist_to_segment(x, y, 36, 37, 46, 26)
            )
            if check <= 2.5:
                color = SIGNAL
            row.append(color)
        rows.append(row)
    return png(size, rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "icon-192.png").write_bytes(draw(192, maskable=False))
    (OUT / "icon-512.png").write_bytes(draw(512, maskable=False))
    (OUT / "icon-maskable-512.png").write_bytes(draw(512, maskable=True))
    print("ok")


if __name__ == "__main__":
    main()
