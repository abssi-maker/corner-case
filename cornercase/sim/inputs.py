"""Per-intersection simulator inputs. Every value records where it came from.

Order of preference: measured from video (step 2) > NYC / MTA open data > labelled
assumption. No crash data is used anywhere here, so the trust check is not circular.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path

from ..data.geo import dist_m

ROOT = Path(__file__).resolve().parents[2]
CORRIDOR = ROOT / "data" / "corridor.json"
MEASURED = ROOT / "data" / "vision" / "measurements.json"

MEASURED_SITE = "w45"              # the corner the footage shows (7 Av / Broadway at W 45 St)
TIMES_SQ_PLAZA = "Times Square Plaza"

AVE_VPH = 1072  # DOT ATVC (7ym2-wayt) 2022, 7 Av SB at Times Square, 17:00-18:00 mean
COUNT_PERIOD_H = 2.0   # DOT bi-annual counts treated as 2-hour period totals (assumption)
CROSS_SHARE = 0.5      # share of passing walkers who cross at a given corner (assumption)


@dataclass
class Value:
    v: float
    source: str
    kind: str  # "measured" | "open-data" | "assumption"

    def __float__(self):
        return float(self.v)


@dataclass
class NodeInputs:
    id: str
    name: str
    street_num: int
    cross_dirs: list[str]
    crossing_m: dict[str, float]           # leg -> length
    ped_vph: Value                         # crossings / hour, all four legs
    ped_leg_share: dict[str, float]
    ave_vph: Value
    cross_vph: dict[str, Value]            # direction -> veh/h
    ave_turn_share: Value                  # share of avenue traffic turning each available way
    cross_turn_share: Value                # share of cross-street traffic turning onto 7 Av
    turn_speed_mps: Value
    through_speed_mps: Value
    walk_speed_mps: Value
    noncompliance: Value                   # share of arrivals at DON'T WALK who cross anyway
    detect_turn: Value                     # chance a turning driver sees a walker in time
    detect_through: Value
    lpi_s: Value
    excl_ped_s: Value
    cycle_s: Value
    ave_share: Value
    flags: dict = field(default_factory=dict)

    def sources(self) -> list[dict]:
        out = []
        for k, v in asdict(self).items():
            if isinstance(v, dict) and {"v", "source", "kind"} <= set(v):
                out.append({"input": k, **v})
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    if isinstance(vv, dict) and {"v", "source", "kind"} <= set(vv):
                        out.append({"input": f"{k}.{kk}", **vv})
        return out


def A(v, src):
    return Value(v, src, "assumption")


def O(v, src):
    return Value(v, src, "open-data")


def M(v, src):
    return Value(v, src, "measured")


def _rank_factor(rank):
    return {1: 1.0, 2: 0.75, 3: 0.55, 4: 0.4, 5: 0.3}.get(rank or 3, 0.55)


def _plaza_mult(plaza):
    return {TIMES_SQ_PLAZA: 1.8, "33rd Street Plaza": 1.2}.get(plaza or "", 1.0)


def exposure(node, alpha):
    s = node["subway_riders_2024_weighted"] / 1e6
    return _rank_factor(node["demand_rank"]) * (1 + alpha * s) * _plaza_mult(node["plaza"])


def fit_ped_scale(nodes, anchors):
    """Fit (P0, alpha) so P0 * exposure matches DOT counts at the anchor points.
    Each anchor is matched to the corridor corners within 120 m of it."""
    pairs = []
    for a in anchors:
        near = [n for n in nodes if dist_m(a["lat"], a["lon"], n["lat"], n["lon"]) < 120]
        if near:
            target = a["mean_count_2023_24"] / COUNT_PERIOD_H * CROSS_SHARE
            pairs.append((near, target))
    best = None
    for i in range(0, 51):
        alpha = i / 100
        ratios = [t / (sum(exposure(n, alpha) for n in near) / len(near)) for near, t in pairs]
        p0 = math.exp(sum(math.log(r) for r in ratios) / len(ratios))
        err = sum((math.log(r) - math.log(p0)) ** 2 for r in ratios)
        if best is None or err < best[0] - 1e-12:
            best = (err, p0, alpha)
    return best[1], best[2], len(pairs)


def load_measured() -> dict:
    if MEASURED.exists():
        return json.loads(MEASURED.read_text()).get("sim_inputs", {})
    return {}


def build_inputs(use_measured: bool = True) -> list[NodeInputs]:
    cor = json.loads(CORRIDOR.read_text())
    nodes, anchors = cor["intersections"], cor.get("ped_count_anchors", [])
    p0, alpha, n_anchor = fit_ped_scale(nodes, anchors)
    meas = load_measured() if use_measured else {}
    out = []
    for n in nodes:
        e = exposure(n, alpha)
        ped = O(round(p0 * e), f"MTA 2024 subway entries within 120 m + DOT pedestrian-demand rank "
                               f"{n['demand_rank']}{' + ' + n['plaza'] if n['plaza'] else ''}, scaled to "
                               f"{n_anchor} DOT bi-annual count points (alpha={alpha:.2f})")
        two_way = len(n["cross_dirs"]) == 2
        per_dir = 350 if two_way else (250 if n["cross_lanes"] <= 1 else 400)
        cross = {d: A(per_dir, f"assumed from {n['cross_lanes']} travel lane(s) (DOT Centerline); "
                               f"no DOT count on this street near 7 Av") for d in n["cross_dirs"]}
        lpi_on = bool(n["lpi_installed"])
        here_measured = use_measured and meas and (n["id"] == MEASURED_SITE or (
            n["plaza"] == TIMES_SQ_PLAZA and n["broadway"]))
        where = "W 45 St footage" + ("" if n["id"] == MEASURED_SITE else ", applied to this Times Square corner")

        def pick(key, default: Value) -> Value:
            if here_measured and key in meas:
                return M(meas[key]["v"], f"{meas[key]['source']} ({where})")
            return default

        out.append(NodeInputs(
            id=n["id"], name=n["name"], street_num=n["street_num"], cross_dirs=n["cross_dirs"],
            crossing_m={"E": n["cross_width_m"], "W": n["cross_width_m"],
                        "N": n["ave_width_m"], "S": n["ave_width_m"]},
            ped_vph=ped,
            ped_leg_share={"E": 0.3, "W": 0.3, "N": 0.2, "S": 0.2},
            ave_vph=(A(150, "7 Av begins here: only the Central Park drive feeds the avenue (assumption)")
                     if n["street_num"] == 59 else
                     O(AVE_VPH, "DOT ATVC 2022, 7 Av SB at Times Square, 17:00 hour; assumed constant along corridor")),
            cross_vph=cross,
            ave_turn_share=pick("ave_turn_share", A(0.10, "assumption: 10% of avenue traffic turns each available way")),
            cross_turn_share=A(0.25, "assumption: 25% of cross-street traffic turns onto 7 Av"),
            turn_speed_mps=pick("turn_speed_mps", A(5.5, "assumption: 20 km/h turning speed")),
            through_speed_mps=A(9.0, "assumption: 32 km/h through an intersection (posted 25 mph, DOT Centerline)"),
            walk_speed_mps=pick("walk_speed_mps", A(1.35, "assumption: mean adult walking speed")),
            noncompliance=pick("noncompliance", A(0.12, "assumption: 12% of walkers arriving on DON'T WALK cross anyway")),
            detect_turn=A(0.90, "assumption: turning driver sees a walker in the crosswalk 90% of the time"),
            detect_through=A(0.75, "assumption: through driver sees a walker crossing against the signal 75% of the time"),
            lpi_s=(O(7.0, f"DOT VZV LPI list: installed {n['lpi_installed'][0]}; duration assumed 7 s")
                   if lpi_on else O(0.0, "not on DOT VZV LPI list")),
            excl_ped_s=(O(20.0, "DOT exclusive pedestrian signal list; phase length assumed 20 s")
                        if n["exclusive_ped_phase"] else O(0.0, "no exclusive pedestrian phase listed")),
            cycle_s=A(90.0, "assumption: NYC typical 90 s cycle"),
            ave_share=A(0.6, "assumption: 60% of green time to 7 Av"),
            flags={"broadway": n["broadway"], "plaza": n["plaza"], "lpi_installed": n["lpi_installed"],
                   "exclusive_ped_phase": n["exclusive_ped_phase"], "lat": n["lat"], "lon": n["lon"],
                   "gx": n["gx"], "gy": n["gy"], "measured_here": bool(here_measured)},
        ))
    return out
