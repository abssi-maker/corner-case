"""The 7th Avenue corridor simulator.

Each intersection is simulated for one hour (default: weekday 5-6 pm) with walkers on
four crosswalk legs and vehicles on up to seven movements. Corners are coupled through
turn bans: a banned turn moves downstream to the next street that runs the same way,
which is how a fix at one corner can push risk onto the next block.

Deterministic: same inputs, plan, future and seeds give identical results.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from . import agents as AG
from . import conflicts as CF
from . import signals as S
from .inputs import NodeInputs
from .interventions import TODAY, Changes, Future

DETOUR_S = 40.0          # extra travel per diverted car (assumption: ~2 blocks)
SAT_HEADWAY_S = 2.0      # per lane (1,800 veh/h/lane)
TURN_HEADWAY_S = 2.6
SEEDS = (11, 23, 37)
DEFAULT_BAN_COMPLIANCE = 0.9


@dataclass(frozen=True)
class Eff:
    """Effective parameters of one corner under a plan and a future."""
    plan: S.SignalPlan
    ped_vph: float
    base_ped_vph: float
    ave_through: float
    ave_left: float
    ave_right: float
    cross_through: float
    cross_turn: float
    base: tuple  # baseline volumes, for CRN ceilings
    turn_speed_left: float
    turn_speed_right: float
    cross_turn_speed: float
    through_speed: float
    walk_mean: float
    noncompliance: float
    detect_turn: float
    detect_through: float
    established_bonus: float
    turn_headway: float
    through_headway: float
    cross_headway: float


def _turn_kinds(dirs):
    """Avenue (southbound) turns available at a corner: left onto eastbound, right onto westbound."""
    return {"left": "EB" in dirs, "right": "WB" in dirs}


def diversions(inputs: list[NodeInputs], plan: dict[str, Changes], future: Future) -> dict[str, dict]:
    """Avenue turning volume after bans. Returns {node: {"left": vph, "right": vph,
    "diverted_out": vph, "diverted_in": vph}}. Traffic flows south (decreasing street)."""
    comp = future.ban_compliance if future.ban_compliance is not None else DEFAULT_BAN_COMPLIANCE
    order = sorted(inputs, key=lambda n: -n.street_num)  # north -> south
    vol = {}
    for n in order:
        kinds = _turn_kinds(n.cross_dirs)
        share = float(n.ave_turn_share)
        base = float(n.ave_vph) * future.veh_mult
        vol[n.id] = {"left": base * share if kinds["left"] else 0.0,
                     "right": base * share if kinds["right"] else 0.0,
                     "diverted_out": 0.0, "diverted_in": 0.0}
    for i, n in enumerate(order):
        ch = plan.get(n.id)
        ban = ch.turn_ban if ch and ch.turn_ban else "none"
        for kind in ("left", "right"):
            if ban not in (kind, "both") or vol[n.id][kind] == 0:
                continue
            moved = vol[n.id][kind] * comp
            vol[n.id][kind] -= moved
            vol[n.id]["diverted_out"] += moved
            # next downstream corner where this turn exists and is not banned
            for m in order[i + 1:]:
                mch = plan.get(m.id)
                mban = mch.turn_ban if mch and mch.turn_ban else "none"
                if _turn_kinds(m.cross_dirs)[kind] and mban not in (kind, "both"):
                    vol[m.id][kind] += moved
                    vol[m.id]["diverted_in"] += moved
                    break
    return vol


def effective(n: NodeInputs, ch: Changes | None, future: Future, turns: dict) -> Eff:
    ch = ch or Changes()
    lpi = ch.lpi_s if ch.lpi_s is not None else float(n.lpi_s)
    plan = S.SignalPlan(cycle_s=ch.cycle_s or float(n.cycle_s), ave_share=ch.ave_share or float(n.ave_share),
                        lpi_s=lpi, split_turns=bool(ch.split_phase), excl_ped_s=float(n.excl_ped_s))
    pm = future.ped_mult_for(n.id)
    sightline = 0.05 if ch.daylighting else 0.0  # mostly helps drivers turning at the corner
    left_speed = float(n.turn_speed_mps) * future.turn_speed_mult * (0.75 if ch.hardened_centerline else 1.0)
    cross_vph = sum(float(v) for v in n.cross_vph.values()) * future.veh_mult
    ave_turning = turns["left"] + turns["right"]
    ave_vph = float(n.ave_vph) * future.veh_mult
    base_turn = float(n.ave_vph) * float(n.ave_turn_share)
    through_lanes = max(1, (4 if n.street_num != 59 else 2) + future.through_lanes_delta)
    return Eff(
        plan=plan,
        ped_vph=float(n.ped_vph) * pm, base_ped_vph=float(n.ped_vph),
        ave_through=max(0.0, ave_vph - (ave_vph * float(n.ave_turn_share) * sum(_turn_kinds(n.cross_dirs).values()))),
        ave_left=turns["left"], ave_right=turns["right"],
        cross_through=cross_vph * (1 - float(n.cross_turn_share)),
        cross_turn=cross_vph * float(n.cross_turn_share),
        base=(float(n.ave_vph), base_turn, sum(float(v) for v in n.cross_vph.values())),
        turn_speed_left=left_speed,
        turn_speed_right=float(n.turn_speed_mps) * future.turn_speed_mult * 0.85,
        cross_turn_speed=float(n.turn_speed_mps) * future.turn_speed_mult * 0.9,
        through_speed=float(n.through_speed_mps),
        walk_mean=float(n.walk_speed_mps) * future.walk_speed_mult,
        noncompliance=float(n.noncompliance) * future.noncompliance_mult,
        detect_turn=float(n.detect_turn) + sightline + future.detect_delta + (0.03 if ch.hardened_centerline else 0.0),
        detect_through=float(n.detect_through) + 0.4 * sightline + future.detect_delta,
        established_bonus=0.04,
        turn_headway=TURN_HEADWAY_S + future.turn_headway_add_s,
        through_headway=SAT_HEADWAY_S / through_lanes,
        cross_headway=SAT_HEADWAY_S / 1.5,
    )


def _key(n: NodeInputs, e: Eff, seed: int, hours: float) -> str:
    return hashlib.blake2b(json.dumps([n.id, repr(e), seed, hours, n.crossing_m, n.ped_leg_share],
                                      sort_keys=True, default=str).encode(), digest_size=12).hexdigest()


_CACHE: dict[str, dict] = {}


def simulate_node(n: NodeInputs, e: Eff, seed: int, hours: float = 1.0, trace_s: float = 0.0) -> dict:
    key = _key(n, e, seed, hours)
    if not trace_s and key in _CACHE:
        return _CACHE[key]
    H = 3600.0 * hours
    plan = e.plan
    legs = {}
    for leg in ("E", "W", "N", "S"):
        share = n.ped_leg_share[leg]
        legs[leg] = AG.pedestrians(seed, n.id, leg, e.ped_vph * share, e.base_ped_vph * share, plan,
                                   n.crossing_m[leg], e.walk_mean, 0.25, e.noncompliance, H)
    base_ave, base_turn, base_cross = e.base
    # (stream, leg, phase, turning, vph, base vph, headway, speed, detect, dist to zone)
    w_cross = n.crossing_m["E"]
    w_ave = n.crossing_m["N"]
    streams = [
        ("ave_left", "E", "A", True, e.ave_left, base_turn, e.turn_headway, e.turn_speed_left, e.detect_turn, w_ave * 0.6 + 4),
        ("ave_right", "W", "A", True, e.ave_right, base_turn, e.turn_headway, e.turn_speed_right, e.detect_turn, w_ave * 0.4 + 3),
        ("cross_turn", "S", "B", True, e.cross_turn, base_cross * 0.25, e.turn_headway, e.cross_turn_speed, e.detect_turn, w_cross * 0.5 + 4),
        ("ave_through_n", "N", "A", False, e.ave_through, base_ave, e.through_headway, e.through_speed, e.detect_through, 2.0),
        ("ave_through_s", "S", "A", False, e.ave_through, base_ave, e.through_headway, e.through_speed, e.detect_through, w_cross + 3),
        ("cross_through_e", "E", "B", False, e.cross_through, base_cross, e.cross_headway, e.through_speed * 0.85, e.detect_through, 2.0 + w_ave * 0.5),
        ("cross_through_w", "W", "B", False, e.cross_through, base_cross, e.cross_headway, e.through_speed * 0.85, e.detect_through, 2.0 + w_ave * 0.5),
    ]
    res = {"near_miss": 0, "serious": 0, "sev": 0.0, "by_stream": {}, "veh_delay_s": 0.0, "vehicles": 0}
    trace_events = [] if trace_s else None
    trace_veh = []
    for name, leg, phase, turning, vph, base_vph, hw, speed, p_det, dist in streams:
        if vph <= 0:
            continue
        windows = S.vehicle_windows(plan, phase, turning, n.crossing_m[leg])
        veh = AG.vehicles(seed, n.id, name, vph, max(base_vph, 1.0), windows, plan.cycle_s, hw, H)
        r = CF.pet_stream(legs[leg], veh, dist_to_zone_m=dist, speed_mps=speed, p_detect=min(p_det, 0.99),
                          established_bonus=e.established_bonus, trace=trace_events, trace_until=trace_s,
                          stream=name, leg=leg)
        ok = np.isfinite(veh["release"])
        delay = (veh["release"][ok] - veh["arrive"][ok]).sum() + r["yield_delay"][ok].sum()
        res["near_miss"] += r["near_miss"]
        res["serious"] += r["serious"]
        res["sev"] += r["sev"]
        res["veh_delay_s"] += float(delay)
        res["vehicles"] += int(ok.sum())
        res["by_stream"][name] = {"near_miss": r["near_miss"], "sev": round(r["sev"], 3), "vehicles": int(ok.sum())}
        if trace_s:
            sel = ok & (r["t_zone"] < trace_s)
            for a, rel, tz, ln in zip(veh["arrive"][sel], veh["release"][sel], r["t_zone"][sel], veh["lane"][sel]):
                trace_veh.append({"stream": name, "leg": leg, "arrive": round(float(a), 2),
                                  "release": round(float(rel), 2), "t_zone": round(float(tz), 2),
                                  "lane": round(float(ln), 2), "speed": round(speed, 1)})
    peds = np.concatenate([legs[l]["start"] - legs[l]["arrive"] for l in legs])
    res["ped_wait_s"] = float(peds.mean()) if len(peds) else 0.0
    res["peds"] = int(len(peds))
    res["against"] = int(sum(legs[l]["against"].sum() for l in legs))
    if trace_s:
        res["trace"] = {
            "events": trace_events, "vehicles": trace_veh,
            "peds": [{"leg": l, "start": round(float(s), 2), "speed": round(float(v), 2), "side": int(sd),
                      "len": n.crossing_m[l], "against": bool(ag)}
                     for l in legs for s, v, sd, ag in zip(legs[l]["start"], legs[l]["speed"], legs[l]["side"], legs[l]["against"])
                     if s < trace_s],
            "signal": {"cycle_s": plan.cycle_s, "ga": plan.ga, "b0": plan.b0, "gb": plan.gb, "lpi_s": plan.lpi_s,
                       "excl_ped_s": plan.excl_ped_s, "split_turns": plan.split_turns,
                       "walk": {l: S.walk_windows(plan, l, n.crossing_m[l]) for l in legs}},
            "crossing_m": n.crossing_m,
        }
    if not trace_s:
        _CACHE[key] = res
    return res


def simulate(inputs: list[NodeInputs], plan: dict[str, Changes] | None = None, future: Future = TODAY,
             seeds=SEEDS, hours: float = 1.0) -> dict:
    """Simulate the corridor; returns per-node metrics (means over seeds) and totals."""
    plan = plan or {}
    turns = diversions(inputs, plan, future)
    nodes = {}
    tot = {"near_miss_ph": 0.0, "serious_ph": 0.0, "sev_ph": 0.0, "veh_delay_h": 0.0, "detour_h": 0.0}
    for n in inputs:
        e = effective(n, plan.get(n.id), future, turns[n.id])
        runs = [simulate_node(n, e, s, hours) for s in seeds]
        nm = np.array([r["near_miss"] for r in runs]) / hours
        sv = np.array([r["sev"] for r in runs]) / hours
        se = np.array([r["serious"] for r in runs]) / hours
        dl = np.array([r["veh_delay_s"] for r in runs]) / hours
        vh = np.array([r["vehicles"] for r in runs]) / hours
        detour = turns[n.id]["diverted_out"] * DETOUR_S
        nodes[n.id] = {
            "near_miss_ph": round(float(nm.mean()), 2), "near_miss_sd": round(float(nm.std()), 2),
            "sev_ph": round(float(sv.mean()), 3), "serious_ph": round(float(se.mean()), 2),
            "veh_delay_s_per_veh": round(float(dl.sum() / max(vh.sum(), 1)), 2),
            "veh_delay_h": round(float(dl.mean()) / 3600, 3),
            "detour_h": round(detour / 3600, 3),
            "ped_wait_s": round(float(np.mean([r["ped_wait_s"] for r in runs])), 1),
            "peds_ph": round(float(np.mean([r["peds"] for r in runs])) / hours),
            "against_ph": round(float(np.mean([r["against"] for r in runs])) / hours),
            "diverted_in_vph": round(turns[n.id]["diverted_in"]),
            "diverted_out_vph": round(turns[n.id]["diverted_out"]),
            "by_stream": runs[0]["by_stream"],
        }
        tot["near_miss_ph"] += nodes[n.id]["near_miss_ph"]
        tot["serious_ph"] += nodes[n.id]["serious_ph"]
        tot["sev_ph"] += nodes[n.id]["sev_ph"]
        tot["veh_delay_h"] += nodes[n.id]["veh_delay_h"]
        tot["detour_h"] += nodes[n.id]["detour_h"]
    tot = {k: round(v, 3) for k, v in tot.items()}
    tot["car_delay_h"] = round(tot["veh_delay_h"] + tot["detour_h"], 3)
    return {"future": future.id, "nodes": nodes, "totals": tot}


def trace_node(inputs: list[NodeInputs], node_id: str, plan: dict[str, Changes] | None = None,
               future: Future = TODAY, seconds: float = 180.0, seed: int = SEEDS[0]) -> dict:
    plan = plan or {}
    turns = diversions(inputs, plan, future)
    n = next(x for x in inputs if x.id == node_id)
    e = effective(n, plan.get(node_id), future, turns[node_id])
    r = simulate_node(n, e, seed, hours=seconds / 3600, trace_s=seconds)
    return {"node": node_id, "seconds": seconds, **r["trace"], "near_miss": r["near_miss"]}


def clear_cache():
    _CACHE.clear()
