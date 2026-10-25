"""Przybliżona lokalizacja placówki z kodu TERYT, gdy NFZ nie podał współrzędnych.

W API 1.4 większość kolejek nie ma współrzędnych, ale każda ma `teryt-place` (7 cyfr:
województwo, powiat, gmina, rodzaj gminy). Bierzemy środek gminy, a dla dzielnic miast
na prawach powiatu (kody spoza listy gmin) średnią środków gmin powiatu. Dane:
`termin/data/gminy.csv` (PRG GUGiK, licencja w `gminy.LICENSE.txt`).
"""

from __future__ import annotations

import csv
from functools import lru_cache
from importlib import resources


@lru_cache(maxsize=1)
def _tables() -> tuple[dict[str, tuple[float, float]], dict[str, tuple[float, float]]]:
    gminy: dict[str, tuple[float, float]] = {}
    sums: dict[str, list[float]] = {}
    text = resources.files("termin.data").joinpath("gminy.csv").read_text(encoding="utf-8")
    for row in csv.DictReader(text.splitlines()):
        point = (float(row["lat"]), float(row["lon"]))
        gminy[row["teryt"]] = point
        acc = sums.setdefault(row["teryt"][:4], [0.0, 0.0, 0.0])
        acc[0] += point[0]
        acc[1] += point[1]
        acc[2] += 1
    powiaty = {code: (a[0] / a[2], a[1] / a[2]) for code, a in sums.items()}
    return gminy, powiaty


def approx_point(teryt: str | None) -> tuple[float, float] | None:
    """(szerokość, długość) środka gminy albo powiatu dla kodu TERYT; None, gdy nieznany."""
    code = "".join(ch for ch in (teryt or "") if ch.isdigit())
    if len(code) < 4:
        return None
    gminy, powiaty = _tables()
    return gminy.get(code[:7]) or powiaty.get(code[:4])
