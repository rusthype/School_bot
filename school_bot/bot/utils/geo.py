from __future__ import annotations

import math

EARTH_RADIUS_M = 6371000


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Return distance between two points in meters (rounded)."""
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    d_lat = lat2_rad - lat1_rad
    d_lon = lon2_rad - lon1_rad

    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(d_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return int(round(EARTH_RADIUS_M * c))
