"""Small geodesy helpers: local metric projection around Midtown."""
from __future__ import annotations

import math

import numpy as np

LAT0, LON0 = 40.7580, -73.9855  # Times Square
M_PER_DEG_LAT = 111_132.0
M_PER_DEG_LON = 111_320.0 * math.cos(math.radians(LAT0))
# Manhattan's street grid is rotated ~29 degrees clockwise from true north
GRID_DEG = 29.0


def to_xy(lat, lon):
    """Degrees -> metres east/north of Times Square."""
    lat = np.asarray(lat, float)
    lon = np.asarray(lon, float)
    return (lon - LON0) * M_PER_DEG_LON, (lat - LAT0) * M_PER_DEG_LAT


def to_grid(lat, lon):
    """Degrees -> metres in Manhattan grid axes (x along streets toward the East
    River, y along avenues uptown)."""
    x, y = to_xy(lat, lon)
    a = math.radians(GRID_DEG)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def dist_m(lat1, lon1, lat2, lon2):
    x1, y1 = to_xy(lat1, lon1)
    x2, y2 = to_xy(lat2, lon2)
    return np.hypot(np.asarray(x1) - x2, np.asarray(y1) - y2)
