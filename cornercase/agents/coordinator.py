"""Corridor coordinator (Muse Wonder): combines corner proposals, rejects fixes that push
risk onto other blocks, chooses stress futures, and sends broken fixes back for revision."""
from __future__ import annotations

import json
import time

from pydantic import BaseModel, ValidationError

from ..meta import MetaClient, MetaError, output_text
from ..sim.interventions import FUTURES, Changes
from .tools import CORRIDOR_DELAY_LIMIT, SimTool, parse_changes

STRESS_IDS = [f for f in FUTURES if f != "today"]
FUND_USD = 100_000  # quarterly capital for this corridor: an assumption, set with --budget

PLAN_SCHEMA = {
    "name": "corridor_plan",
    "schema": {
        "type": "object", "additionalProperties": False, "required": ["briefs", "note"],
        "properties": {
            "briefs": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "priority", "tests", "objective", "focus"],
                "properties": {"id": {"type": "string"},
                               "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                               "tests": {"type": "integer", "description": "simulator tests allowed, 1-5"},
                               "objective": {"type": "string"},
                               "focus": {"type": "string"}}}},
            "note": {"type": "string"},
        },
    },
}

PLAN_SYSTEM = """You lead a team of street-safety agents, one per intersection of 7th Avenue (W 30th St to
Central Park South), working for a city traffic-safety engineer. Before they start, write each
corner agent a brief: priority (high/medium/low), how many simulator tests it may run (high 4-5,
medium 2-3, low 1-2: spend effort where risk is), a one-sentence objective with a number (e.g.
"cut simulated risk by a third with under 4 s added car delay"), and a focus (which conflict to
attack, and what to avoid, e.g. "left turns onto the eastbound street; do not divert turns into
W 41 St, which is already high"). Use both the simulated risk and the real crash history.
Every corner gets a brief. Reply with the JSON plan."""

PLAN_TOOL = {
    "type": "function", "name": "simulate_plan",
    "description": "Simulate the corridor with only the listed corners' proposals accepted. Returns "
                   "corridor risk, added car delay, and per-corner change in risk vs today.",
    "parameters": {"type": "object", "additionalProperties": False, "required": ["accept"],
                   "properties": {"accept": {"type": "array", "items": {"type": "string"},
                                             "description": "corner ids, e.g. w45"}}},
}

REVIEW_SCHEMA = {
    "name": "corridor_review",
    "schema": {
        "type": "object", "additionalProperties": False,
        "required": ["accepted", "rejected", "futures", "note"],
        "properties": {
            "accepted": {"type": "array", "items": {"type": "string"}},
            "rejected": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["id", "reason"],
                "properties": {"id": {"type": "string"}, "reason": {"type": "string"}}}},
            "futures": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["id", "why"],
                "properties": {"id": {"type": "string", "enum": STRESS_IDS}, "why": {"type": "string"}}}},
            "note": {"type": "string"},
        },
    },
}

SYSTEM = f"""You coordinate the street-safety agents of 7th Avenue (W 30th St to Central Park South)
for a city traffic-safety engineer who must decide what to fund this quarter. Each corner agent
proposed a change and tested it alone; each proposal has a cited cost estimate.
Your job:
1. Accept the set of fixes that cuts the most simulated risk within the quarterly budget (the
   midpoint costs of accepted fixes must not exceed it) and keeps the corridor's added car delay
   under {CORRIDOR_DELAY_LIMIT:.0%} of today's. Test combinations with simulate_plan (at most 3 calls):
   fixes interact through diverted turns.
2. Reject every other proposal with a specific reason: it pushes risk onto another corner, it does
   not fit the budget (say what it would cost and what it buys), or it buys too little risk cut.
3. Choose 3 or 4 stress futures (from: {", ".join(STRESS_IDS)}) most likely to break THIS plan,
   and say why for each.
Numbers are simulated near-misses, never injuries. Reply with the JSON review."""


class Brief(BaseModel):
    id: str
    priority: str
    tests: int
    objective: str
    focus: str


def corridor_table(tool: SimTool, crashes: dict) -> list[dict]:
    rows = []
    for n in tool.inputs:
        r = tool.today["nodes"][n.id]
        worst = max(r["by_stream"].items(), key=lambda kv: kv[1]["sev"])[0] if r["by_stream"] else ""
        rows.append({"id": n.id, "corner": n.name, "sim_risk": r["sev_ph"], "near_misses_ph": r["near_miss_ph"],
                     "worst_conflict": worst, "people_hit_2023_26": crashes.get(n.id, {}).get("people_hit", 0),
                     "killed": crashes.get(n.id, {}).get("killed", 0), "walk_head_start_s": float(n.lpi_s),
                     "cross_street": "two-way" if len(n.cross_dirs) == 2 else n.cross_dirs[0]})
    return rows


class Review(BaseModel):
    accepted: list[str]
    rejected: list[dict]
    futures: list[dict]
    note: str


def proposal_table(agents: dict, tool: SimTool) -> list[dict]:
    rows = []
    for nid, a in agents.items():
        d = a.decision
        if not d or d.decision != "keep":
            continue
        ch = parse_changes(d.changes)
        test = next((t["result"] for t in reversed(a.tests)
                     if t["changes"] == ch.model_dump(exclude_none=True) and t["result"].get("future") == "today"), None)
        rows.append({"id": nid, "corner": tool.names[nid], "change": "; ".join(ch.describe()),
                     "risk_before": test and test["risk_before"], "risk_after": test and test["risk"],
                     "car_delay_added_s": test and round(test["car_delay_s_per_veh"] - test["car_delay_before"], 1),
                     "risk_pushed_elsewhere": test and test["risk_pushed_elsewhere"],
                     "spillover": test and test["spillover"]})
    return rows


def plan_summary(tool: SimTool, plan: dict[str, Changes], future_id="today", count=True) -> dict:
    r = tool.run_plan(plan, future_id, count=count)
    b = tool.base(future_id)
    per = {k: round(v["sev_ph"] - b["nodes"][k]["sev_ph"], 2) for k, v in r["nodes"].items()}
    added = r["totals"]["car_delay_h"] - b["totals"]["car_delay_h"]
    return {"future": future_id, "risk": r["totals"]["sev_ph"], "risk_before": b["totals"]["sev_ph"],
            "near_misses_ph": r["totals"]["near_miss_ph"], "near_misses_before": b["totals"]["near_miss_ph"],
            "added_car_delay_h": round(added, 2), "added_car_delay_share": round(added / b["totals"]["car_delay_h"], 4),
            "risk_change_by_corner": {k: v for k, v in per.items() if abs(v) >= 0.5}, "raw": r}


class Coordinator:
    def __init__(self, client: MetaClient, tool: SimTool, emit=None):
        self.c = client
        self.tool = tool
        self.emit = emit or (lambda e: None)
        self.last_id = None
        self.events: list[dict] = []

    def _event(self, state, **kw):
        ev = {"t": time.time(), "state": state, **kw}
        self.events.append(ev)
        self.emit({"type": "coordinator", **ev})

    def plan(self, crashes: dict) -> dict[str, dict]:
        """Lead agent: brief every corner agent before it starts (objective, focus, test budget)."""
        rows = corridor_table(self.tool, crashes)
        self._event("planning", corners=len(rows))
        body = self.c.respond("wonder", [{"role": "user", "content": "Corridor today (simulated 5-6 pm) and real "
                              "pedestrian crash history:\n" + json.dumps(rows) + "\nWrite the briefs."}],
                              stage="coordinator:plan", schema=PLAN_SCHEMA, reasoning="medium",
                              instructions=PLAN_SYSTEM, max_output_tokens=12000)
        briefs: dict[str, dict] = {}
        try:
            data = json.loads(output_text(body))
            for b in data.get("briefs", []):
                bb = Brief(**b)
                if bb.id in self.tool.names:
                    briefs[bb.id] = {**bb.model_dump(), "tests": max(1, min(5, bb.tests))}
            self.plan_note = data.get("note", "")
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            self._event("repair", message=f"plan unreadable, using defaults: {str(e)[:120]}")
            self.plan_note = ""
        for r in rows:  # every corner gets a brief, even if the model skipped one
            briefs.setdefault(r["id"], {"id": r["id"], "priority": "medium", "tests": 3,
                                        "objective": "cut simulated risk without pushing it elsewhere",
                                        "focus": r["worst_conflict"], "default": True})
        self._event("briefed", briefs={k: {kk: v[kk] for kk in ("priority", "tests")} for k, v in briefs.items()})
        return briefs

    def review(self, agents: dict, costs: dict, budget: float = FUND_USD) -> tuple[Review, dict[str, Changes]]:
        from .costs import change_kinds
        rows = proposal_table(agents, self.tool)
        for r in rows:
            ks = change_kinds(parse_changes(agents[r["id"]].decision.changes).model_dump())
            lo = sum(costs[k]["low_usd"] for k in ks if k in costs)
            hi = sum(costs[k]["high_usd"] for k in ks if k in costs)
            r["cost_usd_mid"] = round((lo + hi) / 2)
            r["cost_usd_range"] = [round(lo), round(hi)]
        self.costs_mid = {r["id"]: r["cost_usd_mid"] for r in rows}
        self.budget = budget
        proposals = {r["id"]: parse_changes(agents[r["id"]].decision.changes) for r in rows}
        combined = plan_summary(self.tool, proposals) if proposals else None
        self._event("reviewing", proposals=len(rows))
        msg = (f"Quarterly budget: ${budget:,.0f} (midpoint costs). Corner proposals (each tested alone, "
               "simulated weekday 5-6 pm):\n" + json.dumps(rows) +
               "\n\nAll proposals combined: " + json.dumps({k: v for k, v in (combined or {}).items() if k != "raw"}) +
               f"\nAll proposals together cost ${sum(self.costs_mid.values()):,.0f}. Review them.")
        body = self.c.respond("wonder", [{"role": "user", "content": msg}], stage="coordinator:review",
                              tools=[PLAN_TOOL], schema=REVIEW_SCHEMA, reasoning="medium",
                              instructions=SYSTEM, max_output_tokens=8000)
        for _ in range(4):
            calls = [o for o in body.get("output", []) if o.get("type") == "function_call"]
            if not calls:
                break
            outs = []
            for call in calls:
                try:
                    ids = [i for i in json.loads(call.get("arguments") or "{}").get("accept", []) if i in proposals]
                    s = plan_summary(self.tool, {i: proposals[i] for i in ids})
                    res = {k: v for k, v in s.items() if k != "raw"}
                    self._event("testing", accept=ids, result={k: res[k] for k in ("risk", "risk_before", "added_car_delay_share")})
                except (json.JSONDecodeError, RuntimeError) as e:
                    res = {"error": str(e)}
                outs.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(res)})
            body = self.c.respond("wonder", outs, stage="coordinator:review", tools=[PLAN_TOOL],
                                  schema=REVIEW_SCHEMA, previous_response_id=body["id"], reasoning="medium",
                                  instructions=SYSTEM, max_output_tokens=8000)
        self.last_id = body["id"]
        review = self._parse(body, set(proposals))
        accepted = {i: proposals[i] for i in review.accepted}
        for r in review.rejected:
            self._event("rejected", node=r["id"], reason=r["reason"])
        self._event("futures", futures=review.futures)
        return review, accepted

    def _parse(self, body, valid_ids) -> Review:
        text = output_text(body)
        for attempt in range(2):
            try:
                rv = Review(**json.loads(text))
                bad = [i for i in rv.accepted if i not in valid_ids]
                fut = [f["id"] for f in rv.futures]
                if bad:
                    raise ValueError(f"accepted ids that were not proposed: {bad}")
                spend = sum(self.costs_mid.get(i, 0) for i in rv.accepted)
                if spend > self.budget * 1.0001:
                    raise ValueError(f"accepted fixes cost ${spend:,.0f}, over the ${self.budget:,.0f} budget")
                missing = valid_ids - set(rv.accepted) - {r["id"] for r in rv.rejected}
                if missing:
                    raise ValueError(f"every proposal needs a decision; missing: {sorted(missing)}")
                if not 3 <= len(set(fut)) <= 4:
                    raise ValueError("choose 3 or 4 different futures")
                overlap = set(rv.accepted) & {r["id"] for r in rv.rejected}
                if overlap:
                    raise ValueError(f"ids both accepted and rejected: {sorted(overlap)}")
                return rv
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as e:
                if attempt:
                    raise MetaError(f"coordinator: invalid review after repair: {e}")
                self._event("repair", message=str(e)[:200])
                body = self.c.respond("wonder", [{"role": "user", "content":
                                      f"Your review was not valid ({str(e)[:200]}). Reply with only the corrected JSON."}],
                                      stage="coordinator:repair", schema=REVIEW_SCHEMA,
                                      previous_response_id=body["id"], reasoning="low",
                                      instructions=SYSTEM, max_output_tokens=4000)
                text = output_text(body)
        raise AssertionError("unreachable")


def find_breaks(tool: SimTool, plan: dict[str, Changes], future_id: str, today_summary: dict) -> tuple[dict, list[dict]]:
    """A plan breaks under a future when it makes any corner worse than doing nothing in that
    future, when a fix loses most (>70%) of its benefit, or when it blows the car-delay limit.
    (Holiday crowds raise risk everywhere; the question is whether the fixes still help.)"""
    s = plan_summary(tool, plan, future_id)
    base_f = tool.base(future_id)["nodes"]
    raw = s["raw"]["nodes"]
    breaks = []
    for nid, d in s["risk_change_by_corner"].items():
        if d > 1.0:
            culprit = nid if nid in plan else _upstream_culprit(tool, plan, nid)
            breaks.append({"corner": nid, "culprit": culprit, "kind": "worse_than_nothing",
                           "problem": f"{tool.names[nid]} has simulated risk {raw[nid]['sev_ph']:.1f} with the plan vs "
                                      f"{base_f[nid]['sev_ph']:.1f} without it (+{d:.1f})."})
    today_gain = {k: -v for k, v in today_summary["risk_change_by_corner"].items() if k in plan}
    for nid in plan:
        g_today = today_gain.get(nid, 0)
        g_f = base_f[nid]["sev_ph"] - raw[nid]["sev_ph"]
        if g_today > 2.0 and g_f < 0.3 * g_today and not any(b["corner"] == nid for b in breaks):
            breaks.append({"corner": nid, "culprit": nid, "kind": "benefit_lost",
                           "problem": f"the fix at {tool.names[nid]} cuts risk by only {g_f:.1f} under this future "
                                      f"(vs {g_today:.1f} today)."})
    if s["added_car_delay_share"] > 2 * CORRIDOR_DELAY_LIMIT:
        worst = max(plan, key=lambda k: raw[k]["veh_delay_s_per_veh"] - base_f[k]["veh_delay_s_per_veh"])
        breaks.append({"corner": worst, "culprit": worst, "kind": "delay",
                       "problem": f"corridor car delay rises {s['added_car_delay_share']:.0%} (limit "
                                  f"{2 * CORRIDOR_DELAY_LIMIT:.0%} under stress); the biggest share is at {tool.names[worst]}."})
    return s, breaks


def _upstream_culprit(tool: SimTool, plan, nid):
    """The nearest corner north (upstream) of nid whose fix diverts traffic into it."""
    up = sorted((k for k, ch in plan.items() if ch.turn_ban and ch.turn_ban != "none"
                 and int(k[1:]) > int(nid[1:])), key=lambda k: int(k[1:]))
    return up[0] if up else nid
