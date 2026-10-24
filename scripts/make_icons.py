"""Generuje ikony PNG aplikacji (bez zależności graficznych), zgodne z icon.svg.

Znak: bilet z półkolistymi wcięciami (przez które widać tło), białe „T” i pomarańczowa
kropka. Krawędzie wygładzane próbkowaniem 4×4 na piksel.

Użycie: python scripts/make_icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "termin" / "web" / "static" / "icons"
INK = (18, 24, 43)
WHITE = (255, 255, 255)
SIGNAL = (255, 106, 26)
GROUND = (237, 239, 243)
SAMPLES = 4

Color = tuple[int, int, int, int]


def png(width: int, pixels: list[list[Color]]) -> bytes:
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + kind + data + crc

    header = struct.pack(">IIBBBBB", width, width, 8, 6, 0, 0, 0)  # RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def in_round_rect(x: float, y: float, x0: float, y0: float, x1: float, y1: float, r: float) -> bool:
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def mark_color(x: float, y: float) -> tuple[int, int, int] | None:
    """Kolor znaku w punkcie (układ 64×64 jak icon.svg) albo None poza znakiem."""
    if (x - 45) ** 2 + (y - 42) ** 2 <= 4.5**2:
        return SIGNAL
    if (20 <= x <= 44 and 20 <= y <= 26) or (29 <= x <= 35 and 26 <= y <= 44):
        return WHITE
    if not in_round_rect(x, y, 4, 10, 60, 54, 8):
        return None
    if (x - 4) ** 2 + (y - 32) ** 2 < 36 or (x - 60) ** 2 + (y - 32) ** 2 < 36:
        return None  # wcięcia: tło przechodzi przez bilet
    return INK


def draw(size: int, *, background: tuple[int, int, int] | None, scale: float) -> bytes:
    """`background`: pełne tło (ikona maskowalna / Apple) albo przezroczystość."""
    unit = size / 64
    offset = 32 * (1 - scale)
    rows: list[list[Color]] = []
    for py in range(size):
        row: list[Color] = []
        for px in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    u = (px + (sx + 0.5) / SAMPLES) / unit
                    v = (py + (sy + 0.5) / SAMPLES) / unit
                    color = mark_color((u - offset) / scale, (v - offset) / scale)
                    if color is None and background is not None:
                        color = background
                    if color is not None:
                        acc[0] += color[0]
                        acc[1] += color[1]
                        acc[2] += color[2]
                        acc[3] += 255
            n = SAMPLES * SAMPLES
            alpha = acc[3] / n
            if alpha == 0:
                row.append((0, 0, 0, 0))
            else:
                k = acc[3] / 255  # średnia kolorów tylko z próbek pokrytych
                row.append((round(acc[0] / k), round(acc[1] / k), round(acc[2] / k), round(alpha)))
        rows.append(row)
    return png(size, rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "icon-192.png").write_bytes(draw(192, background=None, scale=1.0))
    (OUT / "icon-512.png").write_bytes(draw(512, background=None, scale=1.0))
    # maskowalna: pełne tło w kolorze strony, znak w strefie bezpiecznej (~62%)
    (OUT / "icon-maskable-512.png").write_bytes(draw(512, background=GROUND, scale=0.62))
    # iOS nie obsługuje przezroczystości ikon: tło w kolorze strony
    (OUT / "icon-apple-180.png").write_bytes(draw(180, background=GROUND, scale=0.78))
    print("ok")


if __name__ == "__main__":
    main()
