"""Czysta geometria na sferze (bez I/O)."""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088

# Prostokąt obejmujący Polskę z zapasem: współrzędne poza nim uznajemy za błędne.
POLAND_LAT = (48.9, 55.0)
POLAND_LON = (14.0, 24.3)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Odległość po ortodromie w kilometrach."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def in_poland(lat: float, lon: float) -> bool:
    return POLAND_LAT[0] <= lat <= POLAND_LAT[1] and POLAND_LON[0] <= lon <= POLAND_LON[1]
