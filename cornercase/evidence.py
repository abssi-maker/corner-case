"""Step 4 trust check: does the simulator rank corners the way real crashes did?

The simulator sees no crash data. It is scored against crashes from the held-out
period (2025-01-01 to 2026-10-31), snapped to corners by lat/lon. Baselines: rank by
traffic volume alone, by pedestrian exposure alone, and by 2023-24 crash history.
Confidence intervals: bootstrap over corners (2,000 resamples).

    uv run python -m cornercase.evidence      # writes evidence/trust_check.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .data import crashes as CR
from .sim import corridor as SIM
from .sim.inputs import build_inputs

ROOT = Path(__file__).resolve().parent.parent
EVID = ROOT / "evidence"
PRIMARY = "sev_ph"  # declared before scoring: severity-weighted near-misses per hour


def rankdata(x) -> np.ndarray:
    x = np.asarray(x, float)
    order = np.argsort(x, kind="mergesort")
    r = np.empty(len(x))
    r[order] = np.arange(1, len(x) + 1)
    for v in np.unique(x):
        m = x == v
        if m.sum() > 1:
            r[m] = r[m].mean()
    return r


def spearman(a, b) -> float:
    ra, rb = rankdata(a), rankdata(b)
    if ra.std() == 0 or rb.std() == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def topk_overlap(score, truth, k) -> int:
    """How many of the k corners with the most real crashes are in the score's top k
    (ties in truth broken toward inclusion)."""
    score, truth = np.asarray(score, float), np.asarray(truth, float)
    top_s = set(np.argsort(-score, kind="mergesort")[:k])
    cutoff = np.sort(truth)[::-1][k - 1]
    top_t = set(np.nonzero(truth >= cutoff)[0])
    return min(k, len(top_s & top_t))


def bootstrap_ci(a, b, n=2000, seed=7):
    rng = np.random.default_rng(seed)
    a, b = np.asarray(a, float), np.asarray(b, float)
    vals = []
    for _ in range(n):
        i = rng.integers(0, len(a), len(a))
        vals.append(spearman(a[i], b[i]))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return round(float(lo), 3), round(float(hi), 3)


def permutation_p(a, b, n=5000, seed=11) -> float:
    rng = np.random.default_rng(seed)
    obs = spearman(a, b)
    b = np.asarray(b, float)
    hits = sum(spearman(a, rng.permutation(b)) >= obs for _ in range(n))
    return round((hits + 1) / (n + 1), 4)


def score(name, label, kind, x, y):
    return {"id": name, "label": label, "kind": kind,
            "spearman": round(spearman(x, y), 3), "ci95": bootstrap_ci(x, y),
            "p_one_sided": permutation_p(x, y),
            "top5": topk_overlap(x, y, 5), "top10": topk_overlap(x, y, 10)}


def trust_check(use_measured: bool = True, sim_result: dict | None = None) -> dict:
    inputs = build_inputs(use_measured=use_measured)
    ids = [n.id for n in inputs]
    names = {n.id: n.name for n in inputs}
    cor = json.loads((ROOT / "data" / "corridor.json").read_text())["intersections"]
    snapped = CR.snap(CR.load(), cor)
    held = CR.per_node(snapped, ids, CR.HELDOUT)
    hist = CR.per_node(snapped, ids, CR.TRAIN)
    y = [held[i]["crashes"] for i in ids]
    sim = sim_result or SIM.simulate(inputs)
    x_sim = [sim["nodes"][i][PRIMARY] for i in ids]
    x_vol = [float(n.ave_vph) + sum(float(v) for v in n.cross_vph.values()) for n in inputs]
    x_ped = [float(n.ped_vph) for n in inputs]
    x_hist = [hist[i]["crashes"] for i in ids]
    methods = [
        score("simulator", "Simulator: severity-weighted near-misses per hour", "SIMULATED", x_sim, y),
        score("volume", "Baseline: traffic volume alone", "REAL inputs", x_vol, y),
        score("walkers", "Baseline: walker exposure alone (subway + demand + counts)", "REAL inputs", x_ped, y),
        score("history", "Baseline: crash history 2023-24", "REAL", x_hist, y),
    ]
    sim_secondary = {m: round(spearman([sim["nodes"][i][m] for i in ids], y), 3)
                     for m in ("near_miss_ph", "serious_ph")}
    order_sim = sorted(ids, key=lambda i: -sim["nodes"][i][PRIMARY])
    order_real = sorted(ids, key=lambda i: (-held[i]["crashes"], -held[i]["injured"]))
    s = methods[0]
    verdict = (f"Simulator's top 5 contains {s['top5']} of the real top 5; rank correlation "
               f"{s['spearman']:+.2f} (95% CI {s['ci95'][0]:+.2f} to {s['ci95'][1]:+.2f}).")
    return {
        "held_out": {"from": CR.HELDOUT[0].isoformat(), "to": CR.HELDOUT[1].isoformat(),
                     "crashes": int(sum(y)), "corners": len(ids), "snap_m": CR.SNAP_M},
        "primary_metric": PRIMARY, "use_measured_inputs": use_measured,
        "methods": methods, "simulator_secondary_metrics": sim_secondary, "verdict": verdict,
        "lists": {
            "simulator": [{"id": i, "name": names[i], "value": sim["nodes"][i][PRIMARY]} for i in order_sim[:10]],
            "real": [{"id": i, "name": names[i], "crashes": held[i]["crashes"], "injured": held[i]["injured"],
                      "killed": held[i]["killed"]} for i in order_real[:10]],
        },
        "per_corner": [{"id": i, "name": names[i], "sim": sim["nodes"][i][PRIMARY], "heldout": held[i]["crashes"],
                        "history": hist[i]["crashes"], "volume": x_vol[k], "walkers": x_ped[k]}
                       for k, i in enumerate(ids)],
        "notes": ["No crash data is used to build simulator inputs or to tune parameters.",
                  "Small counts (about 3 held-out crashes per corner) make every ranking noisy; read the intervals."],
    }


if __name__ == "__main__":
    EVID.mkdir(exist_ok=True)
    out = {"with_measured_inputs": trust_check(True), "without_measured_inputs": trust_check(False)}
    (EVID / "trust_check.json").write_text(json.dumps(out, indent=1))
    for k, v in out.items():
        print(k, v["verdict"])
        for m in v["methods"]:
            print(f"  {m['label']:<62} rho={m['spearman']:+.3f} ci={m['ci95']} p={m['p_one_sided']} top5={m['top5']} top10={m['top10']}")
