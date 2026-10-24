"""Generuje ikony PNG aplikacji (bez zależności graficznych): kalendarz z ptaszkiem.

Użycie: python scripts/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "termin" / "web" / "static" / "icons"
BLUE = (11, 79, 138, 255)
WHITE = (255, 255, 255, 255)
LIGHT = (207, 227, 247, 255)
GREEN = (10, 107, 69, 255)
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


def draw(size: int, *, maskable: bool) -> bytes:
    # Układ w jednostkach 64×64 (jak icon.svg). Wariant maskable ma pełne tło i
    # treść zmniejszoną do strefy bezpiecznej (80%).
    scale = size / 64
    shrink = 0.72 if maskable else 1.0
    offset = 32 * (1 - shrink)
    rows = []
    for py in range(size):
        row = []
        for px in range(size):
            u = (px + 0.5) / scale
            v = (py + 0.5) / scale
            if maskable:
                color = BLUE
            else:
                color = BLUE if inside_round_rect(u, v, 0, 0, 64, 64, 14) else CLEAR
            x = (u - offset) / shrink
            y = (v - offset) / shrink
            if inside_round_rect(x, y, 14, 17, 50, 50, 5):
                color = LIGHT if y < 26 else WHITE
            if inside_round_rect(x, y, 21, 11, 26, 22, 2) or inside_round_rect(
                x, y, 38, 11, 43, 22, 2
            ):
                color = WHITE
            if (
                min(dist_to_segment(x, y, 23, 38, 29, 44), dist_to_segment(x, y, 29, 44, 41, 31))
                <= 2.5
            ):
                color = GREEN
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
