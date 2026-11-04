"""Agent value check: at the same simulation budget, is the Wonder plan better than
random search or greedy one-change-at-a-time over the whole corridor?

All three are scored by the same objective, evaluated with the same simulator:
    J = corridor risk (severity-weighted near-misses/h)
        + a steep penalty when added car delay exceeds the corridor limit
and also under every stress future the coordinator chose (robustness).

    uv run python -m cornercase.agent_value runs/<id>.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from .agents.tools import CORRIDOR_DELAY_LIMIT, parse_changes
from .sim import corridor as SIM
from .sim.inputs import build_inputs
from .sim.interventions import FUTURES, Changes

ROOT = Path(__file__).resolve().parent.parent

MENU = [
    {"lpi_s": 4}, {"lpi_s": 7}, {"lpi_s": 10},
    {"daylighting": True}, {"hardened_centerline": True}, {"split_phase": True},
    {"turn_ban": "left"}, {"turn_ban": "right"}, {"turn_ban": "both"},
    {"cycle_s": 70}, {"cycle_s": 110}, {"ave_share": 0.5}, {"ave_share": 0.68},
]


def objective(inputs, plan, today, future="today", base=None) -> tuple[float, dict]:
    r = SIM.simulate(inputs, plan, FUTURES[future])
    base = base or today
    added = (r["totals"]["car_delay_h"] - base["totals"]["car_delay_h"]) / base["totals"]["car_delay_h"]
    pen = 400.0 * max(0.0, added - CORRIDOR_DELAY_LIMIT)
    return r["totals"]["sev_ph"] + pen, {"risk": r["totals"]["sev_ph"], "added_delay_share": round(added, 4),
                                         "feasible": added <= CORRIDOR_DELAY_LIMIT + 1e-9}


def _valid(menu_item, node):
    tb = menu_item.get("turn_ban")
    if tb == "left" and "EB" not in node.cross_dirs:
        return False
    if tb == "right" and "WB" not in node.cross_dirs:
        return False
    return True


def random_search(inputs, today, budget, seed=0):
    rng = np.random.default_rng(seed)
    best = (objective(inputs, {}, today)[0], {})
    for _ in range(budget):
        plan = {}
        for n in rng.choice(inputs, size=rng.integers(1, 9), replace=False):
            opts = [m for m in MENU if _valid(m, n)]
            picks = rng.choice(len(opts), size=rng.integers(1, 3), replace=False)
            d = {}
            for p in picks:
                d.update(opts[p])
            plan[n.id] = Changes(**d)
        j, _ = objective(inputs, plan, today)
        if j < best[0]:
            best = (j, plan)
    return best


def greedy(inputs, today, budget):
    """Corners in order of today's risk; at each, try single changes and keep any that
    lowers J (stacking on what was kept)."""
    plan: dict[str, Changes] = {}
    cur = objective(inputs, plan, today)[0]
    used = 0
    order = sorted(inputs, key=lambda n: -today["nodes"][n.id]["sev_ph"])
    for n in order:
        for m in MENU:
            if used >= budget:
                return cur, plan
            if not _valid(m, n):
                continue
            base = plan.get(n.id, Changes()).model_dump(exclude_none=True)
            trial = dict(plan)
            trial[n.id] = Changes(**{**base, **m})
            j, _ = objective(inputs, trial, today)
            used += 1
            if j < cur - 1e-9:
                cur, plan = j, trial
    return cur, plan


def check(run_path: Path) -> dict:
    run = json.loads(run_path.read_text())
    inputs = build_inputs()
    today = SIM.simulate(inputs)
    budget = run["sim_budget"]["corridor_evaluations"]
    wonder_plan = {k: parse_changes(v["changes_raw"]) for k, v in
                   ((i["id"], i) for i in run["step7"]["items"])}
    futures = [s["future"] for s in run["step6"]["stress"]]
    rows = {}
    rnd_j, rnd_plan = random_search(inputs, today, budget)
    gr_j, gr_plan = greedy(inputs, today, budget)
    for name, plan in (("Muse Wonder agents", wonder_plan), ("Random search", rnd_plan),
                       ("Greedy, one change at a time", gr_plan)):
        j, info = objective(inputs, plan, today)
        under = {}
        for f in futures:
            base_f = SIM.simulate(inputs, {}, FUTURES[f])
            jf, inf = objective(inputs, plan, today, f, base_f)
            under[f] = {"risk": inf["risk"], "risk_no_plan": base_f["totals"]["sev_ph"], "feasible": inf["feasible"]}
        rows[name] = {"objective": round(j, 2), **info, "corners_changed": len(plan),
                      "risk_cut_pct": round(100 * (1 - info["risk"] / today["totals"]["sev_ph"]), 1),
                      "under_futures": under,
                      "plan": {k: v.model_dump(exclude_none=True) for k, v in plan.items()}}
    return {"run": run["id"], "budget_corridor_evaluations": budget, "risk_today": today["totals"]["sev_ph"],
            "corridor_delay_limit": CORRIDOR_DELAY_LIMIT, "methods": rows,
            "note": "Same simulator, seeds and objective for all three; random and greedy get exactly as many "
                    "corridor simulations as the agents used. Lower objective is better."}


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs" / ((ROOT / "runs" / "latest.txt").read_text().strip() + ".json")
    out = check(p)
    (ROOT / "evidence").mkdir(exist_ok=True)
    (ROOT / "evidence" / "agent_value.json").write_text(json.dumps(out, indent=1))
    run = json.loads(p.read_text())
    run["agent_value"] = out
    p.write_text(json.dumps(run))
    for k, v in out["methods"].items():
        print(f"{k:<32} J={v['objective']:8.2f} risk cut {v['risk_cut_pct']:5.1f}%  delay +{v['added_delay_share']:.1%} "
              f"corners {v['corners_changed']}")
