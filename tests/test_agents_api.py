"""Agent logic and the API, without network calls: the Meta client is replaced by a fake."""
import json

import pytest
from fastapi.testclient import TestClient

from cornercase.agents.coordinator import find_breaks, plan_summary
from cornercase.agents.tools import SimTool
from cornercase.agents.wonder import CornerAgent, Decision
from cornercase.sim.inputs import build_inputs
from cornercase.sim.interventions import Changes


class FakeClient:
    """Scripted Responses API: first a simulate() call, then a decision."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def respond(self, role, input, **kw):
        self.calls.append({"role": role, "input": input, **{k: v for k, v in kw.items() if k != "tools"}})
        return self.script.pop(0)


def fn_call(args, cid="c1"):
    return {"id": "r1", "output": [{"type": "function_call", "call_id": cid, "name": "simulate", "arguments": json.dumps(args)}]}


def final(obj):
    return {"id": "r2", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(obj)}]}]}


NULLS = {"lpi_s": None, "turn_ban": None, "hardened_centerline": None, "daylighting": None, "split_phase": None,
         "cycle_s": None, "ave_share": None}


@pytest.fixture(scope="module")
def tool():
    return SimTool(build_inputs(use_measured=False))


def test_agent_tests_then_keeps(tool):
    n = next(x for x in tool.inputs if x.id == "w42")
    ch = {**NULLS, "lpi_s": 7, "future": None}
    dec = {"decision": "keep", "changes": {**NULLS, "lpi_s": 7}, "reason": "Risk fell in the simulator."}
    c = FakeClient([fn_call(ch), final(dec)])
    a = CornerAgent(c, tool, n)
    d = a.propose()
    assert d.decision == "keep" and len(a.tests) == 1
    states = [e["state"] for e in a.events]
    assert states[:3] == ["proposing", "testing", "measured"] and states[-1] == "kept"
    out = c.calls[1]["input"][0]
    assert out["type"] == "function_call_output" and "risk_before" in json.loads(out["output"])


def test_agent_repairs_keep_without_change(tool):
    n = next(x for x in tool.inputs if x.id == "w42")
    bad = {"decision": "keep", "changes": NULLS, "reason": "x"}
    good = {"decision": "drop", "changes": NULLS, "reason": "Nothing helped."}
    c = FakeClient([final(bad), final(good)])
    a = CornerAgent(c, tool, n)
    d = a.propose()
    assert d.decision == "drop" and d.headline == "No change"
    assert any(e["state"] == "repair" for e in a.events)


def test_drop_with_changes_is_normalised(tool):
    n = next(x for x in tool.inputs if x.id == "w42")
    a = CornerAgent(FakeClient([]), tool, n)
    d = a.check(Decision(decision="drop", changes={**NULLS, "daylighting": True}, reason="r"))
    assert d.changes == {}


def test_untested_kept_change_is_simulated_by_the_system(tool):
    n = next(x for x in tool.inputs if x.id == "w42")
    a = CornerAgent(FakeClient([]), tool, n)
    a.tests = [{"changes": {"lpi_s": 7.0}, "result": {}}]
    a.check(Decision(decision="keep", changes={**NULLS, "daylighting": True}, reason="r"))
    assert a.tests[-1]["by"] == "system" and a.tests[-1]["changes"] == {"daylighting": True}
    assert any(e["state"] == "verifying" for e in a.events)


def test_tool_errors_are_returned_as_data(tool):
    res, ch = tool.tool_call("w42", {**NULLS, "lpi_s": 99, "future": None})
    assert "error" in res and ch is None
    res, _ = tool.tool_call("w42", {**NULLS, "future": "mars"})
    assert "error" in res


def test_turn_ban_spillover_is_reported_and_breaks(tool):
    rep = tool.corner_report("w44", Changes(turn_ban="left"))
    assert rep["risk_pushed_elsewhere"] > 0 and rep["spillover"]
    plan = {"w44": Changes(turn_ban="left")}
    today = plan_summary(tool, plan, count=False)
    _, breaks = find_breaks(tool, plan, "today", today)
    assert any(b["kind"] == "worse_than_nothing" and b["culprit"] == "w44" for b in breaks)


def test_holiday_crowds_alone_do_not_break_a_helpful_fix(tool):
    plan = {"w42": Changes(lpi_s=7, daylighting=True)}
    today = plan_summary(tool, plan, count=False)
    _, breaks = find_breaks(tool, plan, "holiday", today)
    assert not breaks


def test_coordinator_plan_fills_missing_briefs(tool):
    from cornercase.agents.coordinator import Coordinator
    data = {"briefs": [{"id": "w42", "priority": "high", "tests": 9, "objective": "cut risk by a third",
                        "focus": "left turns"}], "note": "n"}
    co = Coordinator(FakeClient([final(data)]), tool)
    briefs = co.plan({})
    assert briefs["w42"]["tests"] == 5 and len(briefs) == len(tool.inputs)
    assert briefs["w30"].get("default")


def test_api_lists_runs():
    from cornercase.api import app
    c = TestClient(app)
    r = c.get("/api/runs")
    assert r.status_code == 200 and "runs" in r.json()
    assert c.get("/").status_code == 200
    assert c.get("/static/app.js").status_code == 200
    assert c.get("/api/runs/does-not-exist").status_code == 404
