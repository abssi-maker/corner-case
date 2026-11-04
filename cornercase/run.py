"""A full Corner Case run: all seven steps, written to runs/<id>.json.

    uv run python -m cornercase.run            # live: Muse Wonder agents + Spark (needs META_API_KEY)
    uv run python -m cornercase.run --replay runs/<id>.json   # prints a summary, no API calls

The run file is self-contained: the UI's "Play the story" mode reads only it (plus the
display clip), so judges can replay a recorded run with no key.
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from . import evidence as EV
from .agents import costs as COSTS
from .agents import glimmer_triage as GT
from .agents import glimmer_watch as GW
from .agents.coordinator import FUND_USD, Coordinator, find_breaks, plan_summary
from .agents.tools import CORNER_DELAY_LIMIT_S, CORRIDOR_DELAY_LIMIT, SimTool, parse_changes
from .agents.wonder import CornerAgent
from .data import crashes as CR
from .data.geo import to_grid
from .meta import MetaClient, MetaError
from .sim import corridor as SIM
from .sim.inputs import build_inputs
from .sim.interventions import EFFECTS, FUTURES, future_to_dict

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
VISION = ROOT / "data" / "vision"
DEFAULT_CORNER = "w45"
MAX_COST_USD = 12.0
BUILD_FIRST = 3          # corners built first; the rest are funded once camera counts confirm
SUCCESS_SHARE = 0.5      # expand if camera close calls fall by at least this share of the simulated cut
SUCCESS_WEEKS = 6


def plan_cost_mid(plan: dict, costs: dict) -> dict[str, float]:
    out = {}
    for nid, ch in plan.items():
        ks = COSTS.change_kinds(ch.model_dump())
        out[nid] = round(sum((costs[k]["low_usd"] + costs[k]["high_usd"]) / 2 for k in ks if k in costs))
    return out


def _json(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(type(o))


# ---------------------------------------------------------------- step 1 (REAL)
def where_people_get_hurt(inputs) -> dict:
    cor = json.loads((ROOT / "data" / "corridor.json").read_text())["intersections"]
    allc = CR.load()
    snapped = CR.snap(allc, cor)
    ids = [n["id"] for n in cor]
    per = CR.per_node(snapped, ids)
    # every crash along the corridor (corners and mid-block) for the map
    gx_ave = float(np.mean([n["gx"] for n in cor]))
    y_lo, y_hi = min(n["gy"] for n in cor) - 40, max(n["gy"] for n in cor) + 10
    gx, gy = to_grid([c["lat"] for c in allc], [c["lon"] for c in allc])
    corridor = [dict(c, gx=round(float(x), 1), gy=round(float(y), 1)) for c, x, y in zip(allc, gx, gy)
                if abs(x - gx_ave) < 30 and y_lo <= y <= y_hi]
    snapped_ids = {c["id"]: c["node"] for c in snapped}
    dots = [{"lat": c["lat"], "lon": c["lon"], "gx": c["gx"], "gy": c["gy"], "date": c["date"],
             "injured": c["injured"], "killed": c["killed"], "factor": c["factor"],
             "vehicle": c["vehicle"], "node": snapped_ids.get(c["id"])} for c in corridor]
    hurt = sum(c["injured"] + c["killed"] for c in corridor)
    killed = sum(c["killed"] for c in corridor)
    nodes = []
    for n in cor:
        p = per[n["id"]]
        nodes.append({**{k: n[k] for k in ("id", "name", "street_num", "lat", "lon", "gx", "gy", "cross_dirs",
                                           "plaza", "broadway", "lpi_installed", "exclusive_ped_phase")},
                      "crashes": p["crashes"], "people_hit": p["injured"] + p["killed"], "killed": p["killed"]})
    first = min(c["date"] for c in corridor) if corridor else None
    return {
        "headline": {"people_hit": hurt, "killed": killed, "crashes": len(corridor), "blocks": 29,
                     "since": "2023-01-01", "through": CR.HELDOUT[1].isoformat(), "first_crash": first},
        "corner_totals": {"people_hit": sum(n["people_hit"] for n in nodes), "crashes": sum(n["crashes"] for n in nodes)},
        "nodes": nodes, "crashes": dots, "gx_ave": round(gx_ave, 1),
        "source": "NYC Open Data, Motor Vehicle Collisions - Crashes (h9gi-nx95), Manhattan, pedestrians injured or "
                  "killed, Jan 2023 - Oct 31 2026; located by lat/lon (within 30 m of 7 Av, W 30 St to Central Park S)",
        "snap_m": CR.SNAP_M,
    }


# ---------------------------------------------------------------- step 2 (REAL, measured)
def watch_the_corner() -> dict:
    m = json.loads((VISION / "measurements.json").read_text())
    tracks = json.loads((VISION / "tracks.json").read_text())
    calls = json.loads((VISION / "calls.json").read_text()) if (VISION / "calls.json").exists() else {}
    return {"measurements": m, "tracks": tracks, "video": "media/corner.mp4", "calls": calls.get("calls", []),
            "analysed_at": calls.get("at")}


# ---------------------------------------------------------------- step 3 (SIMULATED)
def rebuild(inputs, today) -> dict:
    traces = {n.id: SIM.trace_node(inputs, n.id, seconds=180.0) for n in inputs}
    return {
        "today": today, "traces": traces,
        "inputs": {n.id: n.sources() for n in inputs},
        "hour": "weekday 5-6 pm", "seeds": list(SIM.SEEDS),
        "method": {"near_miss": "post-encroachment time < 1.5 s between a walker and a vehicle at the crosswalk",
                   "risk": "near-misses weighted by (vehicle speed / 9 m/s)^2, a kinetic-energy proxy",
                   "effects": EFFECTS},
    }


# ---------------------------------------------------------------- run
class Run:
    def __init__(self, emit=None):
        self.emit_cb = emit or (lambda e: None)
        self.timeline: list[dict] = []
        self.t0 = time.time()

    def emit(self, ev: dict):
        ev = {"at": round(time.time() - self.t0, 2), **ev}
        self.timeline.append(ev)
        self.emit_cb(ev)

    def step(self, n: int, status: str, msg: str = ""):
        self.emit({"type": "step", "step": n, "status": status, "msg": msg})

    def _checkpoint(self, run_id, agents):
        """Write finished agents so a crashed run can resume without repeating paid calls."""
        done = {nid: {"events": a.events, "tests": a.tests, "last_id": a.last_id,
                      "decision": a.decision.model_dump() if a.decision else None}
                for nid, a in agents.items() if a.decision is not None}
        (RUNS / f"{run_id}.partial.json").write_text(json.dumps({"agents": done}, default=_json))

    def live(self, corners: list[str] | None = None, workers: int = 8, budget: float = FUND_USD,
             resume: str | None = None) -> dict:
        run_id = resume or time.strftime("%Y%m%d-%H%M%S")
        RUNS.mkdir(exist_ok=True)
        client = MetaClient()
        models = client.resolve_models()
        inputs = build_inputs()
        run = {"id": run_id, "mode": "live", "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "seeds": list(SIM.SEEDS), "models": models, "default_corner": DEFAULT_CORNER,
               "limits": {"corner_car_delay_s": CORNER_DELAY_LIMIT_S, "corridor_car_delay_share": CORRIDOR_DELAY_LIMIT}}

        self.step(1, "running", "Locating every pedestrian crash on 7th Ave")
        run["step1"] = where_people_get_hurt(inputs)
        self.step(1, "done", f"{run['step1']['headline']['people_hit']} people hit")

        self.step(2, "running", "Loading SAM 3.1 tracks and Muse Spark notes")
        run["step2"] = watch_the_corner()
        self.step(2, "done", f"{run['step2']['measurements']['sam']['people_tracked']} people tracked")

        self.step(3, "running", "Simulating today's corridor")
        tool = SimTool(inputs)
        run["step3"] = rebuild(inputs, tool.today)
        self.step(3, "done", f"{tool.today['totals']['near_miss_ph']:.0f} simulated near-misses/h")

        self.step(4, "running", "Scoring the simulator against held-out crashes")
        run["step4"] = {"with_measured_inputs": EV.trust_check(True, tool.today),
                        "without_measured_inputs": EV.trust_check(False)}
        self.step(4, "done", run["step4"]["with_measured_inputs"]["verdict"])

        # step 5: the lead coordinator briefs one Wonder sub-agent per corner, then reviews
        self.step(5, "running", "Coordinator briefing the corner agents")
        coord = Coordinator(client, tool, emit=self.emit)
        crashes = {n["id"]: n for n in run["step1"]["nodes"]}
        self.step(5, "running", "Muse Glimmer corner-triage screening all 30 corners")
        triage = GT.triage_corridor(inputs)
        self.emit({"type": "triage", "status": triage["status"], **triage.get("score_vs_simulator", {})})
        briefs = coord.plan(crashes, triage.get("corners"))
        self.step(5, "running", "Corner agents proposing and testing fixes")
        agents = {n.id: CornerAgent(client, tool, n, emit=self.emit, brief=briefs.get(n.id)) for n in inputs
                  if not corners or n.id in corners}
        partial = RUNS / f"{run_id}.partial.json"
        if resume and partial.exists():
            from .agents.wonder import Decision
            for nid, d in json.loads(partial.read_text())["agents"].items():
                if nid in agents and d["decision"]:
                    a = agents[nid]
                    a.events, a.tests, a.last_id = d["events"], d["tests"], d["last_id"]
                    a.decision = Decision(**d["decision"])
        lock = __import__("threading").Lock()

        def go(a):
            if a.decision is not None:
                return a
            for attempt in range(2):  # one fresh retry: a hung or invalid conversation is restarted
                try:
                    if attempt:
                        a.last_id, a.tests = None, []
                        a._event("retrying")
                    a.propose()
                    break
                except MetaError as e:
                    a._event("error", message=str(e)[:300])
            with lock:
                self._checkpoint(run_id, agents)
            return a

        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(go, agents.values()))
        if client.total_cost() > MAX_COST_USD:
            raise MetaError(f"cost guard: ${client.total_cost():.2f} spent")
        all_kinds = sorted(COSTS.ITEMS)
        try:
            costs_all = COSTS.lookup(client, all_kinds)
        except (MetaError, json.JSONDecodeError, KeyError):
            costs_all = COSTS.lookup(None, all_kinds)
        review, plan = coord.review(agents, costs_all, budget, crashes)
        today_sum = plan_summary(tool, plan, "today", count=False)
        run["step5"] = {
            "agents": {nid: {"name": tool.names[nid], "events": a.events, "tests": a.tests, "brief": a.brief,
                             "decision": {**a.decision.model_dump(), "headline": a.decision.headline}
                             if a.decision else None}
                       for nid, a in agents.items()},
            "coordinator": {"review": review.model_dump(), "events": coord.events, "briefs": briefs,
                            "plan_note": getattr(coord, "plan_note", ""), "budget_usd": budget,
                            "costs_mid": coord.costs_mid,
                            "accepted_cost_usd": sum(coord.costs_mid.get(i, 0) for i in review.accepted)},
            "plan_before_stress": {k: v.model_dump(exclude_none=True) for k, v in plan.items()},
            "combined": {k: v for k, v in today_sum.items() if k != "raw"},
            "glimmer_triage": triage,
        }
        self.step(5, "done", f"{len(plan)} fixes accepted, {len(review.rejected)} rejected")

        # step 6: stress futures chosen by the coordinator
        self.step(6, "running", "Stress-testing the plan")
        # the coordinator says which futures it expects to break the plan and why; the system then
        # tests every future anyway (they are cheap, deterministic runs), so a missed one still counts
        picked = [dict(f, picked=True) for f in review.futures]
        futures_all = picked + [{"id": fid, "picked": False, "why": "not picked by the coordinator; the system "
                                 "tests every future"} for fid in FUTURES if fid != "today"
                                and fid not in {f["id"] for f in picked}]
        stress = []
        for f in futures_all:
            fid = f["id"]
            s, breaks = find_breaks(tool, plan, fid, today_sum)
            entry = {"future": fid, "label": FUTURES[fid].label, "why": f["why"], "picked": f["picked"],
                     "first": {k: v for k, v in s.items() if k != "raw"}, "breaks": breaks, "revisions": []}
            self.emit({"type": "stress", "future": fid, "breaks": len(breaks)})
            culprits = list(dict.fromkeys(b["culprit"] for b in breaks if b["culprit"] in agents))[:3]
            for c in culprits:
                left = budget - sum(v for k, v in plan_cost_mid(plan, costs_all).items() if k != c)
                problem = (" ".join(b["problem"] for b in breaks if b["culprit"] == c) +
                           f" Budget left for this corner's fix: ${left:,.0f} (midpoint cost).")
                others = {k: v for k, v in plan.items() if k != c}
                try:
                    d = agents[c].revise(fid, problem, others)
                except MetaError as e:
                    agents[c]._event("error", message=str(e)[:300])
                    continue
                old = plan.get(c)
                if d.decision == "keep":
                    plan[c] = parse_changes(d.changes)
                else:
                    plan.pop(c, None)
                entry["revisions"].append({"node": c, "from": old.model_dump(exclude_none=True) if old else None,
                                           "to": plan[c].model_dump(exclude_none=True) if c in plan else None,
                                           "headline": d.headline, "reason": d.reason})
            # a revision must not break the budget: the corner with the fewest people hit waits
            # (the revised corner included), so the plan stays in harm order
            spent = plan_cost_mid(plan, costs_all)
            entry["budget_drops"] = []
            while sum(spent.values()) > budget * 1.0001 and plan:
                k = min(plan, key=lambda i: (crashes.get(i, {}).get("people_hit", 0), -spent[i]))
                entry["budget_drops"].append({"node": k, "cost_mid": spent[k],
                                              "people_hit": crashes.get(k, {}).get("people_hit", 0),
                                              "reason": f"the revision raised the plan to ${sum(spent.values()):,.0f}; "
                                                        f"{tool.names[k]} has the fewest people hit, so it waits"})
                plan.pop(k)
                spent.pop(k)
            if culprits:
                today_sum = plan_summary(tool, plan, "today", count=False)
                s2, breaks2 = find_breaks(tool, plan, fid, today_sum)
                entry["after"] = {k: v for k, v in s2.items() if k != "raw"}
                entry["breaks_after"] = breaks2
                entry["holds"] = not breaks2
            else:
                entry["holds"] = not breaks
            stress.append(entry)
        # money freed by a budget drop goes back down the harm-ordered list: an agent's tested fix that
        # was rejected only for budget joins if it fits and breaks no future
        backfill = []
        waiting = {d["node"] for e in stress for d in e.get("budget_drops", [])}
        for k in sorted((r["id"] for r in review.rejected), key=lambda i: -crashes.get(i, {}).get("people_hit", 0)):
            a = agents.get(k)
            if k in plan or k in waiting or not a or not a.decision or a.decision.decision != "keep":
                continue
            trial = {**plan, k: parse_changes(a.decision.changes)}
            cost = sum(plan_cost_mid(trial, costs_all).values())
            if cost > budget * 1.0001:
                continue
            trial_today = plan_summary(tool, trial, "today", count=False)
            if any(find_breaks(tool, trial, f["id"], trial_today)[1] for f in futures_all):
                continue
            plan, today_sum = trial, trial_today
            backfill.append({"node": k, "people_hit": crashes.get(k, {}).get("people_hit", 0), "plan_cost_mid": cost})
        final_checks = []
        for f in futures_all:
            s, br = find_breaks(tool, plan, f["id"], today_sum)
            final_checks.append({"future": f["id"], "label": FUTURES[f["id"]].label, "holds": not br,
                                 "risk": s["risk"], "risk_before": s["risk_before"],
                                 "added_car_delay_share": s["added_car_delay_share"], "breaks": br})
        run["step6"] = {"futures": {k: future_to_dict(v) for k, v in FUTURES.items()},
                        "stress": stress, "final_checks": final_checks, "backfill": backfill}
        self.step(6, "done", f"{sum(1 for s in stress if s['breaks'])} future(s) broke the plan")

        # step 7: the brief
        self.step(7, "running", "Pricing the plan with cited sources")
        kinds = sorted({k for ch in plan.values() for k in COSTS.change_kinds(ch.model_dump())})
        costs = {k: costs_all[k] for k in kinds if k in costs_all}  # the same prices the budget was checked with
        final = plan_summary(tool, plan, "today", count=False)
        raw, base = final["raw"]["nodes"], tool.today["nodes"]
        items = []
        for nid, ch in plan.items():
            ks = COSTS.change_kinds(ch.model_dump())
            lo = sum(costs[k]["low_usd"] for k in ks if k in costs)
            hi = sum(costs[k]["high_usd"] for k in ks if k in costs)
            gain = base[nid]["sev_ph"] - raw[nid]["sev_ph"]
            items.append({"id": nid, "name": tool.names[nid], "changes": ch.describe(),
                          "changes_raw": ch.model_dump(exclude_none=True),
                          "risk_before": base[nid]["sev_ph"], "risk_after": raw[nid]["sev_ph"],
                          "near_misses_before": base[nid]["near_miss_ph"], "near_misses_after": raw[nid]["near_miss_ph"],
                          "serious_before": base[nid]["serious_ph"], "serious_after": raw[nid]["serious_ph"],
                          "car_delay_added_s": round(raw[nid]["veh_delay_s_per_veh"] - base[nid]["veh_delay_s_per_veh"], 1),
                          "cost_low": lo, "cost_high": hi, "cost_kinds": ks,
                          "risk_cut_per_10k": round(gain / max((lo + hi) / 2, 1) * 1e4, 3),
                          "people_hit": crashes.get(nid, {}).get("people_hit", 0),
                          "killed": crashes.get(nid, {}).get("killed", 0)})
        # build order: real harm first (people hit), then the bigger simulated cut
        items.sort(key=lambda r: (-r["people_hit"], -(r["risk_before"] - r["risk_after"])))
        for i, it in enumerate(items, 1):
            it["priority"] = i
            it["wave"] = 1 if i <= BUILD_FIRST else 2
            cut = 1 - it["near_misses_after"] / it["near_misses_before"] if it["near_misses_before"] else 0
            it["success_rule"] = {"simulated_cut_pct": round(100 * cut, 1),
                                  "expand_if_camera_cut_pct_at_least": round(100 * cut * SUCCESS_SHARE, 1),
                                  "within_weeks": SUCCESS_WEEKS}
        watcher = GW.run_watcher(f"Corner: 7 Av & W 45 St (Times Square). Change installed: "
                                 f"{'; '.join(next((it['changes'] for it in items if it['id'] == 'w45'), ['none yet']))}.")
        run["step7"] = {
            "items": items, "costs": costs,
            "totals": {"near_misses_before": final["near_misses_before"], "near_misses_after": final["near_misses_ph"],
                       "risk_before": final["risk_before"], "risk_after": final["risk"],
                       "serious_before": tool.today["totals"]["serious_ph"], "serious_after": final["raw"]["totals"]["serious_ph"],
                       "added_car_delay_h": final["added_car_delay_h"], "added_car_delay_share": final["added_car_delay_share"],
                       "cost_low": sum(i["cost_low"] for i in items), "cost_high": sum(i["cost_high"] for i in items),
                       "cost_mid": sum((i["cost_low"] + i["cost_high"]) / 2 for i in items), "budget_usd": budget,
                       "wave1_cost_mid": sum((i["cost_low"] + i["cost_high"]) / 2 for i in items if i["wave"] == 1)},
            "rules": {"build_first": BUILD_FIRST, "order": "people hit 2023-26 (real), then simulated cut",
                      "success": f"expand when camera close calls at a built corner fall by at least "
                                 f"{SUCCESS_SHARE:.0%} of its simulated cut within {SUCCESS_WEEKS} weeks"},
            "after_plan_nodes": raw,
            "plan_traces": {nid: SIM.trace_node(inputs, nid, plan, seconds=180.0) for nid in plan},
            "watcher": watcher,
        }
        self.step(7, "done", f"{len(items)} changes in priority order")

        run["sim_budget"] = {"corridor_evaluations": tool.used, "limit": tool.budget}
        run["calls"] = client.calls
        run["cost_usd"] = {"this_run": client.total_cost(),
                           "vision_pipeline": run["step2"]["measurements"].get("cost_usd")}
        run["timeline"] = self.timeline
        path = RUNS / f"{run_id}.json"
        path.write_text(json.dumps(run, default=_json))
        (RUNS / "latest.txt").write_text(run_id)
        partial.unlink(missing_ok=True)
        self.emit({"type": "done", "run_id": run_id})
        return run


def summary(run: dict) -> str:
    t = run["step7"]["totals"]
    lines = [f"run {run['id']} ({run['mode']}), models {run['models']}",
             f"step 1: {run['step1']['headline']['people_hit']} people hit since 2023",
             f"step 4: {run['step4']['with_measured_inputs']['verdict']}",
             f"step 5: {len(run['step5']['plan_before_stress'])} fixes before stress; "
             f"{len(run['step5']['coordinator']['review']['rejected'])} rejected",
             f"step 6: " + ", ".join(f"{s['label']}: {'broke' if s['breaks'] else 'held'}"
                                     f"{' -> revised, ' + ('holds' if s.get('holds') else 'still breaks') if s['revisions'] else ''}"
                                     for s in run["step6"]["stress"]),
             f"step 7: near-misses/h {t['near_misses_before']:.0f} -> {t['near_misses_after']:.0f}, "
             f"car delay +{t['added_car_delay_share']:.1%}, ${t['cost_low']:,.0f}-${t['cost_high']:,.0f}",
             f"cost ${run['cost_usd']['this_run']:.2f}, {len(run['calls'])} model calls, "
             f"{run['sim_budget']['corridor_evaluations']} simulator runs by agents"]
    return "\n".join(lines)


if __name__ == "__main__":
    if "--replay" in sys.argv:
        print(summary(json.loads(Path(sys.argv[sys.argv.index("--replay") + 1]).read_text())))
    else:
        arg = lambda k: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else None
        corners = arg("--corners").split(",") if arg("--corners") else None
        r = Run(emit=lambda e: print(json.dumps({k: v for k, v in e.items() if k in
                                                ("at", "type", "step", "status", "msg", "node", "state", "headline", "future", "breaks", "message")})))
        out = r.live(corners, budget=float(arg("--budget") or FUND_USD), resume=arg("--resume"))
        print(summary(out))
