"""Two-phase NYC signal with pedestrian intervals, LPI, split turns and exclusive ped phase.

Time within a cycle runs [0, C). Phase A serves 7th Ave; phase B serves the cross street.

    A: [0, ga)            avenue vehicles green from lpi_s (walk head start before that)
       [ga, ga+clr)       yellow + all red
    B: [b0, b0+gb)        cross-street vehicles green from b0+lpi_s
       [b0+gb, C - P)     yellow + all red
    X: [C-P, C)           exclusive pedestrian phase (Barnes dance) if P > 0

Crosswalks parallel to the avenue (E and W legs, crossing the cross street) walk with
phase A; crosswalks across the avenue (N and S legs) walk with phase B. With an
exclusive ped phase, nobody walks during A or B. With split turns, turning vehicles
wait until the parallel walk and flashing interval are over.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

YELLOW_ALLRED_S = 5.0          # 3 s yellow + 2 s all red (assumption, NYC typical)
PED_CLEARANCE_MPS = 1.07       # 3.5 ft/s, MUTCD walking speed for flashing DON'T WALK
MIN_WALK_S = 7.0               # MUTCD minimum walk interval


@dataclass(frozen=True)
class SignalPlan:
    cycle_s: float = 90.0
    ave_share: float = 0.6       # share of green time given to the avenue
    lpi_s: float = 0.0
    split_turns: bool = False
    excl_ped_s: float = 0.0      # exclusive pedestrian phase length (0 = none)

    @property
    def green_total(self) -> float:
        return self.cycle_s - 2 * YELLOW_ALLRED_S - self.excl_ped_s

    @property
    def ga(self) -> float:
        return self.green_total * self.ave_share

    @property
    def gb(self) -> float:
        return self.green_total - self.ga

    @property
    def b0(self) -> float:
        return self.ga + YELLOW_ALLRED_S


def phase_window(plan: SignalPlan, phase: str) -> tuple[float, float]:
    if phase == "A":
        return 0.0, plan.ga
    return plan.b0, plan.b0 + plan.gb


def walk_windows(plan: SignalPlan, leg: str, crossing_m: float):
    """(walk_start, walk_end, flash_end) within the cycle for a crosswalk leg."""
    if plan.excl_ped_s > 0:
        s = plan.cycle_s - plan.excl_ped_s
        flash = min(crossing_m / PED_CLEARANCE_MPS, plan.excl_ped_s - MIN_WALK_S)
        return s, plan.cycle_s - flash, plan.cycle_s
    phase = "A" if leg in ("E", "W") else "B"
    s, e = phase_window(plan, phase)
    flash = crossing_m / PED_CLEARANCE_MPS
    walk_end = max(s + MIN_WALK_S, e - flash)
    return s, walk_end, max(e, walk_end)


def vehicle_windows(plan: SignalPlan, phase: str, turning: bool, conflict_leg_m: float = 0.0):
    """Green interval(s) for a movement within the cycle, as a list of (start, end)."""
    s, e = phase_window(plan, phase)
    if plan.excl_ped_s > 0:
        return [(s, e)]
    start = s + plan.lpi_s
    if turning and plan.split_turns:
        # turns wait for the parallel walk + flash to finish; protected at phase end
        _, _, flash_end = walk_windows(plan, "E" if phase == "A" else "S", conflict_leg_m)
        start = max(start, min(flash_end, e - 6.0))
    return [(start, e)] if e > start else []


def ped_state(plan: SignalPlan, leg: str, crossing_m: float, t: np.ndarray) -> np.ndarray:
    """0 = WALK, 1 = flashing DON'T WALK, 2 = steady DON'T WALK (vectorised)."""
    ws, we, fe = walk_windows(plan, leg, crossing_m)
    tc = np.mod(t, plan.cycle_s)
    st = np.full(tc.shape, 2, dtype=np.int8)
    st[(tc >= we) & (tc < fe)] = 1
    st[(tc >= ws) & (tc < we)] = 0
    return st


def next_walk_start(plan: SignalPlan, leg: str, crossing_m: float, t: np.ndarray) -> np.ndarray:
    ws, _, _ = walk_windows(plan, leg, crossing_m)
    c = plan.cycle_s
    base = np.floor(t / c) * c
    cand = base + ws
    return np.where(cand >= t, cand, cand + c)


def green_release(plan_windows, cycle_s: float, arrivals: np.ndarray, headway_s: float) -> np.ndarray:
    """Queue discharge: each vehicle leaves the stop line at the earliest time that is
    green, not before it arrives, and at least one saturation headway after the one
    ahead. `plan_windows` is a list of (start, end) within the cycle."""
    out = np.empty_like(arrivals)
    if not plan_windows:
        out[:] = np.inf
        return out
    last = -np.inf
    for k, a in enumerate(arrivals):
        t = max(a, last + headway_s)
        for _ in range(4):
            tc = t % cycle_s
            base = t - tc
            ok = False
            nxt = None
            for (s, e) in plan_windows:
                if s <= tc < e:
                    ok = True
                    break
                if tc < s and (nxt is None or s < nxt):
                    nxt = s
            if ok:
                break
            t = base + (nxt if nxt is not None else cycle_s + plan_windows[0][0])
        out[k] = t
        last = t
    return out


def webster_delay(cycle_s: float, green_s: float, flow_vph: float, sat_vph: float) -> float:
    """Webster uniform + overflow delay (s/veh), capped for oversaturation."""
    if flow_vph <= 0:
        return 0.0
    if green_s <= 0:
        return cycle_s
    lam = green_s / cycle_s
    cap = sat_vph * lam
    x = min(flow_vph / max(cap, 1e-6), 1.5)
    d1 = 0.5 * cycle_s * (1 - lam) ** 2 / max(1 - min(x, 1.0) * lam, 1e-3)
    d2 = (x ** 2) / (2 * flow_vph / 3600 * max(1 - x, 0.05))  # Webster second term
    return float(min(d1 + 0.65 * d2, 300.0))
