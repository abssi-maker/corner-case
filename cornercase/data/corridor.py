"""Build the 7th Avenue corridor (W 30th St to Central Park South) from city data.

Sources (all cached in data/opendata/, see data/opendata/SOURCES.md):
- NYC DOT Centerline (inkn-q76z): geometry, traffic direction, lanes, width.
- VZV Leading Pedestrian Interval Signals (xc4v-ntf4): which corners already have an LPI.
- Exclusive Pedestrian Signal (Barnes Dance) Locations (8kuj-2n3u).
- Pedestrian Mobility Plan Pedestrian Demand (fwpa-qxaf): demand rank of each street segment.
- MTA Subway Entrances (i9wp-a4ja) + MTA Hourly Ridership 2024 (wujg-7c2s): walking exposure.
- DOT Bi-Annual Pedestrian Counts (cqsj-cfgu): anchors that scale exposure to people/hour.
- DOT Automated Traffic Volume Counts (7ym2-wayt): the one 7th Ave count in the corridor.

Run `python -m cornercase.data.corridor` to rebuild data/corridor.json.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np

from .geo import dist_m, to_grid

ROOT = Path(__file__).resolve().parents[2]
OD = ROOT / "data" / "opendata"
OUT = ROOT / "data" / "corridor.json"
FT_TO_M = 0.3048

CROSS = [f"W {n} ST" for n in range(30, 59)] + ["CENTRAL PARK S"]


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").upper()).strip()


def _street_num(s: str) -> int | None:
    m = re.search(r"\b(\d{2})\s*(ST|STREET|TH|ND|RD)?\b", _norm(s))
    if "CENTRAL PARK S" in _norm(s) or "59" in _norm(s):
        return 59
    return int(m.group(1)) if m else None


def _coords(rec) -> list[tuple[float, float]]:
    """[(lat, lon)] of all vertices of a centerline record."""
    g = rec["the_geom"]
    lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
    return [(p[1], p[0]) for line in lines for p in line]


def build() -> dict:
    cl = json.loads((OD / "centerline_midtown.json").read_text())
    ave = [r for r in cl if _norm(r.get("full_street_name")) == "7 AVE"]
    ave_pts = np.array([p for r in ave for p in _coords(r)])
    bway = [r for r in cl if _norm(r.get("full_street_name")) == "BROADWAY"]
    bway_pts = np.array([p for r in bway for p in _coords(r)])

    lpi = json.loads((OD / "lpi_midtown.json").read_text())
    barnes = json.loads((OD / "barnes_midtown.json").read_text())
    demand = json.loads((OD / "ped_demand_midtown.json").read_text())
    entrances = json.loads((OD / "subway_entrances_midtown.json").read_text())
    riders = json.loads((OD / "subway_ridership_2024_midtown.json").read_text())
    plazas = json.loads((OD / "plazas_midtown.json").read_text())

    # station complex -> 2024 riders (rows are split by lat/lon; sum them)
    complex_riders: dict[str, float] = {}
    for r in riders:
        complex_riders[r["station_complex_id"]] = complex_riders.get(r["station_complex_id"], 0) + float(r["riders"])
    n_ent: dict[str, int] = {}
    for e in entrances:
        if e.get("entry_allowed") == "YES":
            n_ent[e["complex_id"]] = n_ent.get(e["complex_id"], 0) + 1

    out = []
    for name in CROSS:
        segs = [r for r in cl if _norm(r.get("full_street_name")) == name]
        if not segs:
            continue
        # intersection = the cross-street vertex closest to any 7 Ave vertex
        best = None
        for r in segs:
            for (la, lo) in _coords(r):
                d = dist_m(la, lo, ave_pts[:, 0], ave_pts[:, 1]).min()
                if best is None or d < best[0]:
                    best = (d, la, lo)
        d, lat, lon = best
        if d > 25:
            continue
        # segments touching the intersection (within 25 m of it at an end)
        touching = []
        for r in segs:
            pts = _coords(r)
            if min(dist_m(lat, lon, pts[0][0], pts[0][1]), dist_m(lat, lon, pts[-1][0], pts[-1][1])) < 25:
                touching.append(r)
        dirs = set()
        for r in touching:
            pts = _coords(r)
            gx0, _ = to_grid(*pts[0])
            gx1, _ = to_grid(*pts[-1])
            east = gx1 > gx0
            td = r.get("trafdir")
            if td == "TW":
                dirs |= {"EB", "WB"}
            elif td == "FT":
                dirs.add("EB" if east else "WB")
            elif td == "TF":
                dirs.add("WB" if east else "EB")
        lanes = max(int(r.get("number_travel_lanes") or 1) for r in touching)
        width_ft = max(float(r.get("streetwidth") or 30) for r in touching)
        ave_near = [r for r in ave if min(dist_m(lat, lon, la, lo) for la, lo in _coords(r)) < 25]
        ave_width_ft = max(float(r.get("streetwidth") or 60) for r in ave_near) if ave_near else 60.0
        ave_lanes = max(int(r.get("number_travel_lanes") or 4) for r in ave_near) if ave_near else 4

        num = _street_num(name)
        lpi_hits = [x for x in lpi
                    if dist_m(lat, lon, float(x["lat"]), float(x["long"])) < 60
                    and _street_num(x.get("cross_stree", "") + " " + x.get("main_street", "")) == num]
        # exclusive pedestrian phase at this corner (not a mid-block signal)
        excl_hits = [x for x in barnes
                     if dist_m(lat, lon, x["the_geom"]["coordinates"][1], x["the_geom"]["coordinates"][0]) < 40
                     and not x.get("mid_block_") and "BET" not in _norm(x.get("cross_stre", ""))]
        # Broadway has no roadway centreline through the Times Square plaza; the
        # DOT LPI list names those corners "7 Avenue & Broadway"
        on_bway = (bool(len(bway_pts)) and bool(dist_m(lat, lon, bway_pts[:, 0], bway_pts[:, 1]).min() < 45)) \
            or any("BROADWAY" in _norm(x.get("main_street", "")) for x in lpi_hits)
        plaza = None
        for p in plazas:
            g = p["the_geom"]["coordinates"]
            pts = np.array([(q[1], q[0]) for poly in g for ring in poly for q in ring])
            if dist_m(lat, lon, pts[:, 0], pts[:, 1]).min() < 35:
                plaza = p["plazaname"]
                break

        # cross-street demand rank (1 = highest) from touching PMP segments
        ranks = []
        for s in demand:
            if _street_num(s.get("street", "")) != num or "AVENUE" in s.get("street", ""):
                continue
            g = s["the_geom"]["coordinates"]
            pts = [(p[1], p[0]) for line in g for p in line]
            if min(dist_m(lat, lon, la, lo) for la, lo in pts) < 30:
                ranks.append(int(s["rank"]))
        demand_rank = min(ranks) if ranks else None

        # subway exposure: each entrance gets an equal share of its complex's 2024 riders
        sub = 0.0
        for e in entrances:
            if e.get("entry_allowed") != "YES":
                continue
            dd = dist_m(lat, lon, float(e["entrance_latitude"]), float(e["entrance_longitude"]))
            if dd < 120:
                share = complex_riders.get(e["complex_id"], 0) / max(1, n_ent.get(e["complex_id"], 1))
                sub += share * (1 - dd / 120)  # nearer entrances count more

        gx, gy = to_grid(lat, lon)
        out.append({
            "id": f"w{num}",
            "name": f"7 Av & {'Central Park S' if num == 59 else f'W {num} St'}",
            "street_num": num,
            "lat": round(lat, 6), "lon": round(lon, 6),
            "gx": round(float(gx), 1), "gy": round(float(gy), 1),
            "cross_dirs": sorted(dirs),
            "cross_lanes": lanes,
            "cross_width_m": round(width_ft * FT_TO_M, 1),
            "ave_width_m": round(ave_width_ft * FT_TO_M, 1),
            "ave_lanes": ave_lanes,
            "broadway": on_bway,
            "lpi_installed": sorted({x["install_da"][:10] for x in lpi_hits}),
            "exclusive_ped_phase": bool(excl_hits),
            "plaza": plaza,
            "demand_rank": demand_rank,
            "subway_riders_2024_weighted": round(sub),
        })
    out.sort(key=lambda r: r["street_num"])
    return {"corridor": "7th Avenue, W 30th St to Central Park South", "intersections": out,
            "sources": "see data/opendata/SOURCES.md"}


def ped_anchors() -> list[dict]:
    """DOT bi-annual counts on/at 7th Ave in the corridor: mean of 2023-2024 AM/MD/PM counts."""
    rows = json.loads((OD / "ped_counts_manhattan.json").read_text())
    keep = []
    for r in rows:
        txt = " ".join(_norm(r.get(k)) for k in ("street_nam", "from_stree", "to_street"))
        if "SEVENTH" not in txt and "7 AV" not in txt:
            continue
        vals = [float(v) for k, v in r.items()
                if re.match(r"^(may|oct|june)_?2[34]_", k) and str(v).replace(".", "").isdigit()]
        if not vals:
            continue
        keep.append({"loc": r["loc"], "where": f"{r['street_nam']} ({r['from_stree']} to {r['to_street']})",
                     "mean_count_2023_24": round(sum(vals) / len(vals)), "n": len(vals),
                     "lat": r["the_geom"]["coordinates"][1], "lon": r["the_geom"]["coordinates"][0]})
    return keep


if __name__ == "__main__":
    data = build()
    data["ped_count_anchors"] = ped_anchors()
    OUT.write_text(json.dumps(data, indent=1))
    for r in data["intersections"]:
        print(r["id"], r["cross_dirs"], r["cross_lanes"], r["cross_width_m"], r["ave_width_m"], r["ave_lanes"],
              "BWAY" if r["broadway"] else "", "LPI" if r["lpi_installed"] else "", "EXCL" if r["exclusive_ped_phase"] else "", r["plaza"] or "",
              r["demand_rank"], r["subway_riders_2024_weighted"])
    print(len(data["intersections"]), "intersections"); print(data["ped_count_anchors"])
