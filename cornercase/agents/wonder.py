"""One Muse Wonder sub-agent per intersection.

Each agent reads its corner's facts and today's simulated risk, proposes changes,
runs them through the simulator (a function tool), reads the measured outcome, and
keeps or drops the change. It can later be asked to revise under a stress future,
continuing the same conversation (previous_response_id), so it remembers its tests.
"""
from __future__ import annotations

import json
import time

from pydantic import BaseModel, Field, ValidationError

from ..meta import MetaClient, MetaError, output_text
from ..sim.interventions import EFFECTS, FUTURES
from .tools import CHANGE_PARAMS, CORNER_DELAY_LIMIT_S, SimTool, parse_changes

MAX_TESTS = 4  # default when no coordinator brief

SIM_TOOL = {
    "type": "function", "name": "simulate",
    "description": "Run the 7th Ave corridor simulator (1 hour, 3 seeds) with these changes at YOUR corner. "
                   "Returns simulated near-miss risk at your corner before/after, car delay, walker wait, "
                   "and risk pushed to other corners. Fields set to null keep today's setting.",
    "parameters": {**CHANGE_PARAMS,
                   "properties": {**CHANGE_PARAMS["properties"],
                                  "future": {"type": ["string", "null"], "enum": list(FUTURES) + [None],
                                             "description": "stress future to test under; null = today"}},
                   "required": CHANGE_PARAMS["required"] + ["future"]},
}

DECISION_SCHEMA = {
    "name": "corner_decision",
    "schema": {
        "type": "object", "additionalProperties": False,
        "required": ["decision", "changes", "reason"],
        "properties": {
            "decision": {"type": "string", "enum": ["keep", "drop"]},
            "changes": {k: v for k, v in CHANGE_PARAMS.items()},
            "reason": {"type": "string", "description": "one or two plain sentences (under 300 characters) citing the simulated numbers"},
        },
    },
}


def _safe_describe(args: dict) -> list[str]:
    try:
        return parse_changes({k: v for k, v in args.items() if k != "future"}).describe()
    except (ValidationError, TypeError):
        return []


class Decision(BaseModel):
    decision: str = Field(pattern="^(keep|drop)$")
    changes: dict
    reason: str = Field(max_length=3000)

    @property
    def headline(self) -> str:
        """The UI names a decision by its changes, so the model never writes a title."""
        return " + ".join(parse_changes(self.changes).describe()) or "No change"


def corner_brief(n, today: dict, brief: dict | None = None) -> str:
    r = today["nodes"][n.id]
    dirs = {"EB": "eastbound", "WB": "westbound"}
    turns = []
    if "EB" in n.cross_dirs:
        turns.append("left turns from 7 Av cross the east crosswalk while it shows WALK")
    if "WB" in n.cross_dirs:
        turns.append("right turns from 7 Av cross the west crosswalk while it shows WALK")
    worst = sorted(r["by_stream"].items(), key=lambda kv: -kv[1]["sev"])[:3]
    return (
        f"Corner: {n.name} (7 Av runs one way southbound; cross street "
        f"{' and '.join(dirs[d] for d in n.cross_dirs)}). {'; '.join(turns)}.\n"
        f"Today: walk head start {float(n.lpi_s):g} s ({n.lpi_s.source}); "
        f"{'exclusive pedestrian phase; ' if float(n.excl_ped_s) else ''}"
        f"cycle {float(n.cycle_s):g} s; ~{float(n.ped_vph):.0f} walkers/h crossing ({n.ped_vph.kind}); "
        f"7 Av {float(n.ave_vph):.0f} veh/h; crossings {n.crossing_m['E']:.0f} m (street) and {n.crossing_m['N']:.0f} m (avenue)"
        f"{'; Times Square plaza corner' if n.flags.get('plaza') == 'Times Square Plaza' else ''}.\n"
        f"Simulated today (5-6 pm): risk {r['sev_ph']:.1f} (severity-weighted near-misses/h), "
        f"{r['near_miss_ph']:.0f} near-misses/h, {r['serious_ph']:.0f} serious/h, car delay "
        f"{r['veh_delay_s_per_veh']:.1f} s/veh, walker wait {r['ped_wait_s']:.0f} s, "
        f"{r['against_ph']:.0f} walkers/h cross against the signal.\n"
        f"Biggest conflict streams: " + ", ".join(f"{k} (risk {v['sev']:.1f})" for k, v in worst) + "."
        + (f"\nYour brief from the corridor coordinator: priority {brief['priority']}; objective: {brief['objective']}; "
           f"focus: {brief['focus']}; you may run up to {brief['tests']} simulator tests." if brief else "")
    )


SYSTEM = f"""You are the street-safety agent for one intersection on 7th Avenue, Manhattan, working
for a city traffic-safety engineer. Goal: cut simulated pedestrian near-miss risk at your corner
without pushing risk to other corners and without adding more than {CORNER_DELAY_LIMIT_S:g} s of car
delay per vehicle at your corner.

Changes you can test (combine at most three):
""" + "\n".join(f"- {k}: {v}" for k, v in EFFECTS.items()) + f"""

How to work:
- Read your brief from the corridor coordinator; it sets your objective and test budget.
- Start broad, then narrow: your first turn may call simulate 2-3 times in parallel with clearly
  different options; then refine the best one. Stop as soon as a cheap option meets the objective.
- Read every result: risk before/after, car delay, and risk pushed elsewhere (spillover). A fix
  that pushes risk onto another corner will be rejected by the coordinator.
- Numbers are simulated near-misses, never injuries.
Decide "keep" with exactly one combination you simulated, or "drop" with all changes null if
nothing is worth it within the delay limit. Final answer: the JSON decision."""


class CornerAgent:
    def __init__(self, client: MetaClient, tool: SimTool, node, emit=None, brief: dict | None = None):
        self.brief = brief
        self.c = client
        self.tool = tool
        self.n = node
        self.emit = emit or (lambda e: None)
        self.last_id = None
        self.events: list[dict] = []
        self.decision: Decision | None = None
        self.tests: list[dict] = []

    def _event(self, state, **kw):
        ev = {"t": time.time(), "node": self.n.id, "state": state, **kw}
        self.events.append(ev)
        self.emit({"type": "agent", **ev})

    def _loop(self, first_input, stage, future_id="today", plan=None, max_tests=MAX_TESTS):
        body = self.c.respond("wonder", first_input, stage=stage, tools=[SIM_TOOL], schema=DECISION_SCHEMA,
                              previous_response_id=self.last_id, reasoning="low",
                              instructions=SYSTEM, max_output_tokens=4000)
        tests = 0
        for _ in range(max_tests + 2):
            self.last_id = body["id"]
            calls = [o for o in body.get("output", []) if o.get("type") == "function_call"]
            if not calls:
                break
            outs = []
            for call in calls:
                try:
                    args = json.loads(call.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = None
                if args is None:
                    res, ch = {"error": "arguments were not valid JSON"}, None
                elif tests >= max_tests:
                    res, ch = {"error": "test limit reached; give your decision now"}, None
                else:
                    tests += 1
                    fut = args.get("future")
                    fut = "today" if fut in (None, "", "null", "none") else fut
                    self._event("testing", changes=_safe_describe(args), future=fut)
                    res, ch = self.tool.tool_call(self.n.id, dict(args), future_id, plan)
                    if "error" not in res:
                        self.tests.append({"changes": ch.model_dump(exclude_none=True), "result": res})
                        self._event("measured", changes=ch.describe(), result={
                            k: res[k] for k in ("risk", "risk_before", "car_delay_s_per_veh", "car_delay_before",
                                                "risk_pushed_elsewhere", "future")})
                    else:
                        self._event("error", message=res["error"])
                outs.append({"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(res)})
            body = self.c.respond("wonder", outs, stage=stage, tools=[SIM_TOOL], schema=DECISION_SCHEMA,
                                  previous_response_id=body["id"], reasoning="low",
                                  instructions=SYSTEM, max_output_tokens=4000)
        self.last_id = body["id"]
        return self._decide(body, stage, future_id, plan)

    def check(self, d: Decision, future_id: str = "today", plan=None) -> Decision:
        """Semantic checks beyond the schema. A keep with no change triggers a repair turn;
        a drop that still lists changes is normalised; a kept combination the agent never
        simulated is simulated by the system, so every kept claim is backed by a run."""
        ch = parse_changes(d.changes)
        if d.decision == "keep" and ch.is_empty():
            raise ValueError("decision is keep but no change is set")
        if d.decision == "drop" and not ch.is_empty():
            d = Decision(decision="drop", changes={}, reason=d.reason)
        if d.decision == "keep" and ch.model_dump(exclude_none=True) not in [t["changes"] for t in self.tests]:
            self._event("verifying", changes=ch.describe())
            res, ch2 = self.tool.tool_call(self.n.id, {**ch.model_dump(), "future": None}, future_id, plan)
            if "error" in res:
                raise ValueError(f"the kept changes could not be simulated: {res['error']}")
            self.tests.append({"changes": ch2.model_dump(exclude_none=True), "result": res, "by": "system"})
            self._event("measured", changes=ch2.describe(), by="system", result={
                k: res[k] for k in ("risk", "risk_before", "car_delay_s_per_veh", "car_delay_before",
                                    "risk_pushed_elsewhere", "future")})
        return d

    def _decide(self, body, stage, future_id="today", plan=None) -> Decision:
        text = output_text(body)
        for attempt in range(2):
            try:
                return self.check(Decision(**json.loads(text)), future_id, plan)
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as e:
                if attempt:
                    raise MetaError(f"{self.n.id}: invalid decision after repair: {e}")
                self._event("repair", message=str(e)[:200])
                body = self.c.respond("wonder", [{"role": "user", "content":
                                      f"Your last reply was not a valid decision ({str(e)[:200]}). "
                                      "Reply with only the JSON decision."}],
                                      stage=stage + ":repair", schema=DECISION_SCHEMA,
                                      previous_response_id=body["id"], reasoning="low",
                                      instructions=SYSTEM, max_output_tokens=2000)
                self.last_id = body["id"]
                text = output_text(body)
        raise AssertionError("unreachable")

    def propose(self) -> Decision:
        self._event("proposing")
        msg = [{"role": "user", "content": corner_brief(self.n, self.tool.today, self.brief) +
                "\nPropose and test changes for your corner, then decide."}]
        budget = int(self.brief["tests"]) if self.brief else MAX_TESTS
        d = self._loop(msg, stage=f"wonder:{self.n.id}", max_tests=budget)
        self.decision = d
        self._event("kept" if d.decision == "keep" else "dropped", headline=d.headline, reason=d.reason,
                    changes=parse_changes(d.changes).describe())
        return d

    def revise(self, future_id: str, problem: str, plan) -> Decision:
        self._event("revising", future=future_id, problem=problem)
        msg = [{"role": "user", "content":
                f"Stress test failed. Under the future '{FUTURES[future_id].label}' ({FUTURES[future_id].why}): "
                f"{problem}\nRevise your change so it holds under this future AND today. Test with "
                f"future='{future_id}' and future=null. Then decide."}]
        d = self._loop(msg, stage=f"wonder:{self.n.id}:revise", future_id=future_id, plan=plan, max_tests=3)
        self.decision = d
        self._event("kept" if d.decision == "keep" else "dropped", headline=d.headline, reason=d.reason,
                    changes=parse_changes(d.changes).describe(), revised=True)
        return d
