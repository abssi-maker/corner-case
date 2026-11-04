"""NYC pedestrian crashes, snapped to corridor intersections by lat/lon.

Street names in the crash table are inconsistent and ~28% are blank, so names are
never used: a crash belongs to the nearest intersection within SNAP_M metres.
"""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np

from .geo import to_xy

ROOT = Path(__file__).resolve().parents[2]
CRASHES = ROOT / "data" / "crashes" / "manhattan_ped_2023_2026.json"
SNAP_M = 40.0  # half an avenue block is ~40 m; beyond that a crash is mid-block

TRAIN = (date(2023, 1, 1), date(2024, 12, 31))
HELDOUT = (date(2025, 1, 1), date(2026, 10, 31))


@lru_cache(maxsize=1)
def load() -> list[dict]:
    rows = json.loads(CRASHES.read_text())
    out = []
    for r in rows:
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (40.6 < lat < 40.9 and -74.1 < lon < -73.85):  # (0,0) and other junk
            continue
        out.append({
            "id": r["collision_id"],
            "date": r["crash_date"][:10],
            "time": r.get("crash_time", ""),
            "lat": lat, "lon": lon,
            "injured": int(r.get("number_of_pedestrians_injured") or 0),
            "killed": int(r.get("number_of_pedestrians_killed") or 0),
            "factor": r.get("contributing_factor_vehicle_1", ""),
            "vehicle": r.get("vehicle_type_code1", ""),
        })
    return out


def snap(crashes: list[dict], intersections: list[dict], radius_m: float = SNAP_M) -> list[dict]:
    """Return crashes within radius of an intersection, each with `node` and `dist_m`."""
    if not crashes:
        return []
    cx, cy = to_xy([c["lat"] for c in crashes], [c["lon"] for c in crashes])
    ix, iy = to_xy([i["lat"] for i in intersections], [i["lon"] for i in intersections])
    d = np.hypot(cx[:, None] - ix[None, :], cy[:, None] - iy[None, :])
    j = d.argmin(axis=1)
    best = d[np.arange(len(crashes)), j]
    out = []
    for k in np.nonzero(best <= radius_m)[0]:
        c = dict(crashes[k])
        c["node"] = intersections[j[k]]["id"]
        c["dist_m"] = round(float(best[k]), 1)
        out.append(c)
    return out


def in_period(c: dict, period: tuple[date, date]) -> bool:
    return period[0].isoformat() <= c["date"] <= period[1].isoformat()


def per_node(snapped: list[dict], node_ids: list[str], period=None) -> dict[str, dict]:
    agg = {n: {"crashes": 0, "injured": 0, "killed": 0} for n in node_ids}
    for c in snapped:
        if period and not in_period(c, period):
            continue
        a = agg[c["node"]]
        a["crashes"] += 1
        a["injured"] += c["injured"]
        a["killed"] += c["killed"]
    return agg
