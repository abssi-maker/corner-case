"""The simulator as a tool the agents call, with a shared evaluation budget."""
from __future__ import annotations

import threading

from pydantic import ValidationError

from ..sim import corridor as SIM
from ..sim.interventions import FUTURES, TODAY, Changes

CORNER_DELAY_LIMIT_S = 8.0     # max added car delay per vehicle at a corner
CORRIDOR_DELAY_LIMIT = 0.08    # max added corridor car delay (share of today's)

CHANGE_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "lpi_s": {"type": ["number", "null"], "description": "walk head start in seconds (0-12); null = keep today's"},
        "turn_ban": {"type": ["string", "null"], "enum": ["none", "left", "right", "both", None],
                     "description": "ban turns from 7 Av at this corner"},
        "hardened_centerline": {"type": ["boolean", "null"], "description": "slower left turns"},
        "daylighting": {"type": ["boolean", "null"], "description": "clear parking at the corner for sightlines"},
        "split_phase": {"type": ["boolean", "null"], "description": "turns get their own interval, never during WALK"},
        "cycle_s": {"type": ["number", "null"], "description": "signal cycle length 60-120 s"},
        "ave_share": {"type": ["number", "null"], "description": "share of green to 7 Av, 0.45-0.72"},
    },
    "required": ["lpi_s", "turn_ban", "hardened_centerline", "daylighting", "split_phase", "cycle_s", "ave_share"],
}


def _nullish(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip().lower() in ("", "null", "nil"))


def parse_changes(d: dict | None) -> Changes:
    d = {k: (v.strip().lower() if isinstance(v, str) else v) for k, v in (d or {}).items() if not _nullish(v)}
    return Changes(**d)


class SimTool:
    """Shared by every agent in a run. Counts corridor evaluations (the budget the
    agent-value check compares against)."""

    def __init__(self, inputs, budget: int = 400):
        self.inputs = inputs
        self.names = {n.id: n.name for n in inputs}
        self.budget = budget
        self.used = 0
        self._lock = threading.Lock()
        self.today = SIM.simulate(inputs)
        self._future_base: dict[str, dict] = {"today": self.today}

    def base(self, future_id: str) -> dict:
        if future_id not in self._future_base:
            self._future_base[future_id] = SIM.simulate(self.inputs, {}, FUTURES[future_id])
        return self._future_base[future_id]

    def _spend(self):
        with self._lock:
            if self.used >= self.budget:
                return False
            self.used += 1
            return True

    def run_plan(self, plan: dict[str, Changes], future_id: str = "today", count: bool = True) -> dict:
        if count and not self._spend():
            raise RuntimeError("simulation budget exhausted")
        return SIM.simulate(self.inputs, plan, FUTURES[future_id])

    def corner_report(self, node: str, changes: Changes, future_id: str = "today",
                      plan: dict[str, Changes] | None = None) -> dict:
        """Test `changes` at `node` on top of `plan` (default: today everywhere)."""
        plan = dict(plan or {})
        plan[node] = changes
        base_plan = {k: v for k, v in plan.items() if k != node}
        r = self.run_plan(plan, future_id)
        b = SIM.simulate(self.inputs, base_plan, FUTURES[future_id]) if base_plan else self.base(future_id)
        here, bh = r["nodes"][node], b["nodes"][node]
        spill = []
        for k, v in r["nodes"].items():
            if k == node:
                continue
            d = v["sev_ph"] - b["nodes"][k]["sev_ph"]
            if abs(d) >= 0.5:
                spill.append({"corner": self.names[k], "id": k, "d_risk": round(d, 2),
                              "diverted_in_vph": v["diverted_in_vph"]})
        d_delay = r["totals"]["car_delay_h"] - b["totals"]["car_delay_h"]
        return {
            "corner": self.names[node], "future": future_id,
            "risk": here["sev_ph"], "risk_before": bh["sev_ph"],
            "near_misses_ph": here["near_miss_ph"], "near_misses_before": bh["near_miss_ph"],
            "serious_ph": here["serious_ph"], "serious_before": bh["serious_ph"],
            "car_delay_s_per_veh": here["veh_delay_s_per_veh"], "car_delay_before": bh["veh_delay_s_per_veh"],
            "walker_wait_s": here["ped_wait_s"], "walker_wait_before": bh["ped_wait_s"],
            "crossing_against_ph": here["against_ph"], "crossing_against_before": bh["against_ph"],
            "risk_pushed_elsewhere": round(sum(s["d_risk"] for s in spill if s["d_risk"] > 0), 2),
            "spillover": sorted(spill, key=lambda s: -s["d_risk"])[:4],
            "corridor_added_car_delay_h": round(d_delay + 0.0, 3),
            "within_delay_limit": (here["veh_delay_s_per_veh"] - bh["veh_delay_s_per_veh"]) <= CORNER_DELAY_LIMIT_S,
        }

    def tool_call(self, node: str, args: dict, future_id: str = "today", plan=None) -> tuple[dict, Changes | None]:
        """Run a model's simulate() call; errors come back as data so the agent can recover."""
        try:
            fut = args.pop("future", None)
            # null means today (the revise prompt asks for both future=X and future=null)
            fut = "today" if _nullish(fut) or str(fut).strip().lower() in ("none", "today") else str(fut).strip()
            if fut not in FUTURES:
                return {"error": f"unknown future {fut!r}"}, None
            ch = parse_changes(args)
        except (ValidationError, TypeError) as e:
            detail = "; ".join(x["msg"] for x in e.errors()) if isinstance(e, ValidationError) else str(e)
            return {"error": f"invalid change: {detail}"}, None
        try:
            rep = self.corner_report(node, ch, fut, plan)
        except RuntimeError as e:
            return {"error": str(e) + "; decide with what you have"}, ch
        rep["budget_left"] = self.budget - self.used
        return rep, ch
