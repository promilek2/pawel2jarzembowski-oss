"""Buduje tabelę centroidów kodów pocztowych z GeoNames (CC BY 4.0).

Źródło: https://download.geonames.org/export/zip/PL.zip (licencja CC BY 4.0,
wymaga podania źródła: „GeoNames, https://www.geonames.org/”).

Użycie:
    python scripts/build_postal_centroids.py [ścieżka/do/PL.zip]  [wyjście.csv]

Domyślnie pobiera plik i zapisuje do <data_dir>/postal_codes.csv (patrz `termin config`),
skąd Termin wczyta go automatycznie jako offline'owy geokoder kodów pocztowych.
"""

from __future__ import annotations

import csv
import io
import statistics
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from termin import USER_AGENT
from termin.config import get_settings
from termin.core.provinces import province_code

URL = "https://download.geonames.org/export/zip/PL.zip"


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else get_settings().data_dir / "postal_codes.csv"
    if src is None:
        print(f"pobieram {URL}")
        data = httpx.get(URL, headers={"User-Agent": USER_AGENT}, timeout=60).content
    else:
        data = src.read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        text = zf.read("PL.txt").decode("utf-8")

    groups: dict[str, list[tuple[str, str, float, float]]] = defaultdict(list)
    for row in csv.reader(io.StringIO(text), delimiter="\t"):
        # country, postal_code, place_name, admin1_name, admin1_code, ..., lat, lon, accuracy
        code, name, admin1 = row[1], row[2], row[3]
        province = province_code(admin1) or ""
        groups[code].append((name, province, float(row[9]), float(row[10])))

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["postal_code", "name", "province", "lat", "lon"])
        for code in sorted(groups):
            rows = groups[code]
            writer.writerow(
                [
                    code,
                    rows[0][0],
                    rows[0][1],
                    round(statistics.fmean(r[2] for r in rows), 5),
                    round(statistics.fmean(r[3] for r in rows), 5),
                ]
            )
    print(f"zapisano {len(groups)} kodów do {out}")


if __name__ == "__main__":
    main()
