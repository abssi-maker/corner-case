"""Road users: pedestrians per crosswalk leg and vehicles per movement.

Common random numbers: every stream draws from its own generator keyed by
(seed, node, stream), and arrivals are generated at a fixed ceiling rate then
thinned with a stored uniform. So changing one corner's volume or signal timing
never reshuffles the random draws anywhere else, and option comparisons are fair.
"""
from __future__ import annotations

import hashlib

import numpy as np

from . import signals as S


def rng_for(seed: int, *keys: str) -> np.random.Generator:
    h = hashlib.blake2b(("|".join([str(seed), *keys])).encode(), digest_size=8).digest()
    return np.random.default_rng(int.from_bytes(h, "little"))


def poisson_thinned(rng: np.random.Generator, rate_ph: float, ceiling_ph: float, horizon_s: float):
    """Arrival times of a Poisson process at `rate_ph`, built by thinning one at
    `ceiling_ph` so that the kept arrivals are nested as the rate changes."""
    ceiling_ph = max(ceiling_ph, rate_ph, 1.0)
    n = rng.poisson(ceiling_ph * horizon_s / 3600)
    t = np.sort(rng.uniform(0, horizon_s, n))
    keep = rng.uniform(0, 1, n) < rate_ph / ceiling_ph
    return t, keep


def ceiling(rate_ph: float) -> float:
    """Fixed ceiling bucket so CRN nesting holds for any rate up to 4x the base."""
    return float(2 ** np.ceil(np.log2(max(rate_ph, 1.0) * 4)))


def pedestrians(seed, node_id, leg, rate_ph, base_rate_ph, plan: S.SignalPlan, crossing_m,
                walk_mean, walk_sd, noncompliance, horizon_s=3600.0):
    """Generate walkers on one crosswalk leg; returns dict of arrays."""
    rng = rng_for(seed, node_id, "ped", leg)
    t, keep = poisson_thinned(rng, rate_ph, ceiling(base_rate_ph), horizon_s)
    n = len(t)
    speed = np.clip(rng.normal(walk_mean, walk_sd, n), 0.7, 2.4)
    side = rng.integers(0, 2, n)
    u_comply = rng.uniform(0, 1, n)
    startup = rng.exponential(1.0, n)
    t, speed, side, u_comply, startup = t[keep], speed[keep], side[keep], u_comply[keep], startup[keep]

    state = S.ped_state(plan, leg, crossing_m, t)
    ws, we, fe = S.walk_windows(plan, leg, crossing_m)
    red = plan.cycle_s - (fe - ws)
    # walkers cross against the signal more when the wait is long (assumption: sqrt)
    exp_wait = red * red / (2 * plan.cycle_s)
    p_dont = float(np.clip(noncompliance * np.sqrt(max(exp_wait, 1.0) / 25.0), 0.0, 0.9))
    p_flash = float(np.clip(0.35 + noncompliance, 0.0, 0.95))  # late starters on flashing hand

    go_now = (state == 0) | ((state == 1) & (u_comply < p_flash)) | ((state == 2) & (u_comply < p_dont))
    nxt = S.next_walk_start(plan, leg, crossing_m, t)
    # waiting walkers leave as a platoon spread by start-up reaction
    start = np.where(go_now, t, nxt + startup * 0.8)
    against = (state == 2) & go_now
    return {"arrive": t, "start": start, "speed": speed, "side": side, "against": against,
            "length": crossing_m, "leg": leg}


def vehicles(seed, node_id, stream, rate_ph, base_rate_ph, windows, cycle_s, headway_s,
             horizon_s=3600.0):
    rng = rng_for(seed, node_id, "veh", stream)
    t, keep = poisson_thinned(rng, rate_ph, ceiling(base_rate_ph), horizon_s)
    n = len(t)
    u_detect = rng.uniform(0, 1, n)
    gap = np.clip(rng.lognormal(np.log(2.0), 0.35, n), 0.6, 6.0)  # accepted gap behind a walker (s)
    lane = rng.uniform(0.2, 0.8, n)  # where along the crosswalk the vehicle path crosses
    t, u_detect, gap, lane = t[keep], u_detect[keep], gap[keep], lane[keep]
    release = S.green_release(windows, cycle_s, t, headway_s)
    return {"arrive": t, "release": release, "u_detect": u_detect, "gap": gap, "lane": lane}
