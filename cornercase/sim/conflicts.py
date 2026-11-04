"""Vehicle-pedestrian conflicts at a crosswalk, measured as post-encroachment time.

PET is the time between the first road user leaving the shared conflict zone and the
second one entering it. PET < 1.5 s is a near-miss. Each vehicle contributes at most
one event (its closest walker). Severity weight is (speed at the zone / 9 m/s)^2,
a kinetic-energy proxy; a vehicle that yielded and crept through counts at 3 m/s.
"""
from __future__ import annotations

import numpy as np

NEAR_MISS_PET_S = 1.5
SERIOUS_PET_S = 0.5
ZONE_HALF_M = 1.0      # half-width of the vehicle path across the crosswalk
VEH_LEN_M = 4.6
CREEP_MPS = 3.0
LANE_BINS = np.array([0.3, 0.5, 0.7])  # conflict positions along the crosswalk


def ped_occupancy(peds: dict, pos_frac: float):
    """(enter, exit) times of each walker at a point `pos_frac` along the crosswalk."""
    L = peds["length"]
    d = np.where(peds["side"] == 0, pos_frac * L, (1 - pos_frac) * L)
    enter = peds["start"] + (d - ZONE_HALF_M).clip(0) / peds["speed"]
    exit_ = peds["start"] + (d + ZONE_HALF_M) / peds["speed"]
    order = np.argsort(enter)
    return enter[order], exit_[order], peds["start"][order], peds["against"][order]


def pet_stream(peds: dict, veh: dict, *, dist_to_zone_m: float, speed_mps: float,
               p_detect: float, established_bonus: float = 0.0, follow_s: float = 1.2,
               trace: list | None = None, trace_until: float = 0.0, stream: str = "", leg: str = ""):
    """Run one vehicle stream across one crosswalk; returns summary + per-vehicle delay."""
    occ_full = (VEH_LEN_M + 2 * ZONE_HALF_M) / speed_mps
    occ_slow = (VEH_LEN_M + 2 * ZONE_HALF_M) / CREEP_MPS
    bins = [ped_occupancy(peds, f) for f in LANE_BINS]
    max_cross = (peds["length"] + 2 * ZONE_HALF_M) / max(0.7, float(np.min(peds["speed"])) if len(peds["speed"]) else 1.0)

    n = len(veh["release"])
    t_plan = veh["release"] + dist_to_zone_m / speed_mps
    lane_idx = np.abs(veh["lane"][:, None] - LANE_BINS[None, :]).argmin(axis=1)
    events = 0
    serious = 0
    sev = 0.0
    yield_delay = np.zeros(n)
    prev_t = -np.inf
    for k in range(n):
        if not np.isfinite(t_plan[k]):
            continue
        T = max(t_plan[k], prev_t + follow_s)
        enter, exit_, start, against = bins[lane_idx[k]]
        # walkers whose occupancy could touch [T - 1.5, T + occ + 1.5]
        lo = np.searchsorted(enter, T - max_cross - NEAR_MISS_PET_S)
        hi = np.searchsorted(enter, T + occ_full + NEAR_MISS_PET_S, side="right")
        if hi <= lo:
            prev_t = T
            continue
        e, x, st = enter[lo:hi], exit_[lo:hi], start[lo:hi]
        rel = (x > T - NEAR_MISS_PET_S)
        if not rel.any():
            prev_t = T
            continue
        e, x, st = e[rel], x[rel], st[rel]
        # walkers already established in the crosswalk are easier to see
        established = bool(((T - st) > 3.0).any())
        p = min(0.99, p_detect + (established_bonus if established else 0.0))
        if veh["u_detect"][k] < p:
            g = veh["gap"][k]
            t = T
            for _ in range(12):
                block = (e < t + occ_slow) & (x > t - g)
                if not block.any():
                    break
                t = float(x[block].max()) + g
            v = speed_mps if t <= T + 0.3 else CREEP_MPS
            occ = occ_full if v == speed_mps else occ_slow
            T_final = t
        else:
            v, occ, T_final = speed_mps, occ_full, T
        before = T_final - x[x <= T_final]
        after = e[e >= T_final + occ] - (T_final + occ)
        overlap = (e < T_final + occ) & (x > T_final)
        cands = np.concatenate([before, after, np.zeros(int(overlap.sum()))])
        pet = float(cands.min()) if len(cands) else np.inf
        yield_delay[k] = T_final - t_plan[k]
        prev_t = T_final
        if pet < NEAR_MISS_PET_S:
            events += 1
            w = (v / 9.0) ** 2
            sev += w
            if pet < SERIOUS_PET_S and v > CREEP_MPS:
                serious += 1
            if trace is not None and T_final < trace_until:
                trace.append({"t": round(T_final, 2), "stream": stream, "leg": leg,
                              "lane": round(float(LANE_BINS[lane_idx[k]]), 2), "pet": round(pet, 2),
                              "speed": round(v, 1), "against": bool(np.any(against[lo:hi][rel]))})
    return {"near_miss": events, "serious": serious, "sev": sev, "yield_delay": yield_delay,
            "t_zone": t_plan + yield_delay}
