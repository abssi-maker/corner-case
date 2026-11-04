"""Agent value check: at the same simulation budget, is the Wonder plan better than
random search or greedy one-change-at-a-time over the whole corridor?

All three play by the coordinator's rules and are scored by the same objective, with the same
simulator:
    J = harm-weighted corridor risk: each corner's severity-weighted near-misses/h, weighted by
        the real people hit there 2023-26 (scaled so the weights average 1)
        + a steep penalty when added car delay exceeds the corridor limit
        + a steep penalty when the plan's midpoint cost exceeds the quarterly budget
and also under every stress future (robustness).

    uv run python -m cornercase.agent_value runs/<id>.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from .agents import costs as COSTS
from .agents.coordinator import find_breaks, plan_summary
from .agents.tools import CORRIDOR_DELAY_LIMIT, SimTool, parse_changes
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


class Rules:
    """What every method must respect: harm weights, the budget and the cited unit costs."""
    def __init__(self, run: dict):
        hits = {n["id"]: n["people_hit"] for n in run["step1"]["nodes"]}
        m = float(np.mean(list(hits.values()))) or 1.0
        self.w = {k: v / m for k, v in hits.items()}
        self.budget = run["step5"]["coordinator"].get("budget_usd", 100_000)
        self.costs = COSTS.lookup(None, sorted(COSTS.ITEMS))

    def cost(self, plan) -> float:
        return sum((self.costs[k]["low_usd"] + self.costs[k]["high_usd"]) / 2
                   for ch in plan.values() for k in COSTS.change_kinds(ch.model_dump()) if k in self.costs)

    def harm(self, r) -> float:
        return sum(self.w.get(k, 1.0) * v["sev_ph"] for k, v in r["nodes"].items())


def objective(inputs, plan, today, rules: Rules, future="today", base=None) -> tuple[float, dict]:
    r = SIM.simulate(inputs, plan, FUTURES[future])
    base = base or today
    added = (r["totals"]["car_delay_h"] - base["totals"]["car_delay_h"]) / base["totals"]["car_delay_h"]
    cost = rules.cost(plan)
    pen = 400.0 * max(0.0, added - CORRIDOR_DELAY_LIMIT) + (1000.0 if cost > rules.budget * 1.0001 else 0.0)
    harm = rules.harm(r)
    return harm + pen, {"risk": r["totals"]["sev_ph"], "harm_weighted_risk": round(harm, 2),
                        "near_misses_ph": r["totals"]["near_miss_ph"], "added_delay_share": round(added, 4),
                        "cost_mid_usd": round(cost),
                        "feasible": added <= CORRIDOR_DELAY_LIMIT + 1e-9 and cost <= rules.budget * 1.0001}


def _valid(menu_item, node):
    tb = menu_item.get("turn_ban")
    if tb == "left" and "EB" not in node.cross_dirs:
        return False
    if tb == "right" and "WB" not in node.cross_dirs:
        return False
    return True


def random_search(inputs, today, budget, rules, seed=0):
    rng = np.random.default_rng(seed)
    best = (objective(inputs, {}, today, rules)[0], {})
    for _ in range(budget):
        plan = {}
        for n in rng.choice(inputs, size=rng.integers(1, 9), replace=False):
            opts = [m for m in MENU if _valid(m, n)]
            picks = rng.choice(len(opts), size=rng.integers(1, 3), replace=False)
            d = {}
            for p in picks:
                d.update(opts[p])
            plan[n.id] = Changes(**d)
        j, _ = objective(inputs, plan, today, rules)
        if j < best[0]:
            best = (j, plan)
    return best


def greedy(inputs, today, budget, rules):
    """Corners in the coordinator's order (most people hit first); at each, try single changes
    and keep any that lowers J (stacking on what was kept)."""
    plan: dict[str, Changes] = {}
    cur = objective(inputs, plan, today, rules)[0]
    used = 0
    order = sorted(inputs, key=lambda n: (-rules.w.get(n.id, 0), -today["nodes"][n.id]["sev_ph"]))
    for n in order:
        for m in MENU:
            if used >= budget:
                return cur, plan
            if not _valid(m, n):
                continue
            base = plan.get(n.id, Changes()).model_dump(exclude_none=True)
            trial = dict(plan)
            trial[n.id] = Changes(**{**base, **m})
            j, _ = objective(inputs, trial, today, rules)
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
    rules = Rules(run)
    harm_today = rules.harm(today)
    rows = {}
    rnd_j, rnd_plan = random_search(inputs, today, budget, rules)
    gr_j, gr_plan = greedy(inputs, today, budget, rules)
    for name, plan in (("Muse Wonder agents", wonder_plan), ("Random search", rnd_plan),
                       ("Greedy, one change at a time", gr_plan)):
        j, info = objective(inputs, plan, today, rules)
        under = {}
        for f in futures:
            base_f = SIM.simulate(inputs, {}, FUTURES[f])
            jf, inf = objective(inputs, plan, today, rules, f, base_f)
            under[f] = {"risk": inf["risk"], "risk_no_plan": base_f["totals"]["sev_ph"], "feasible": inf["feasible"]}
        # the coordinator's hard rules, checked the same way for every method
        tool = SimTool(inputs)
        today_sum = plan_summary(tool, plan, "today", count=False)
        worse = sorted(k for k, d in today_sum["risk_change_by_corner"].items() if d > 1.0 and k not in plan)
        broken = sorted(f for f in FUTURES if f != "today" and find_breaks(tool, plan, f, today_sum)[1])
        rows[name] = {"objective": round(j, 2), **info, "corners_changed": len(plan),
                      "corners_made_worse_today": worse, "futures_broken": broken,
                      "risk_cut_pct": round(100 * (1 - info["risk"] / today["totals"]["sev_ph"]), 1),
                      "harm_weighted_cut_pct": round(100 * (1 - info["harm_weighted_risk"] / harm_today), 1),
                      "under_futures": under,
                      "plan": {k: v.model_dump(exclude_none=True) for k, v in plan.items()}}
    return {"run": run["id"], "budget_corridor_evaluations": budget, "risk_today": today["totals"]["sev_ph"],
            "harm_weighted_risk_today": round(harm_today, 2), "budget_usd": rules.budget,
            "corridor_delay_limit": CORRIDOR_DELAY_LIMIT, "methods": rows,
            "note": "Same simulator, seeds, objective, budget and unit costs for all three; random and greedy get "
                    "exactly as many corridor simulations as the agents used. Lower objective is better."}


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs" / ((ROOT / "runs" / "latest.txt").read_text().strip() + ".json")
    out = check(p)
    (ROOT / "evidence").mkdir(exist_ok=True)
    (ROOT / "evidence" / "agent_value.json").write_text(json.dumps(out, indent=1))
    run = json.loads(p.read_text())
    run["agent_value"] = out
    p.write_text(json.dumps(run))
    for k, v in out["methods"].items():
        print(f"{k:<32} J={v['objective']:8.2f} harm-weighted cut {v['harm_weighted_cut_pct']:5.1f}%  risk cut "
              f"{v['risk_cut_pct']:5.1f}%  delay +{v['added_delay_share']:.1%}  ${v['cost_mid_usd']:,}  "
              f"corners {v['corners_changed']}  worse today {v['corners_made_worse_today']}  "
              f"futures broken {len(v['futures_broken'])}/{len(FUTURES) - 1}")
