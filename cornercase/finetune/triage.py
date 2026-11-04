"""Corner triage: the skill the Muse Glimmer LoRA learns from the simulator.

One scenario = one real 7 Av corner with perturbed inputs, under one stress future.
Labels come from the deterministic simulator, never from a model:
  near_miss_ph   simulated near-misses/h at the corner today (not injuries)
  top_conflict   the vehicle stream with the most severity-weighted near-misses
  best_single_change
                 the MENU change at this corner that cuts corridor risk the most
                 (so a turn ban that pushes risk to the next block gets no credit),
                 adding at most DELAY_LIMIT_S of car delay per vehicle at the corner;
                 "none" when no change cuts at least MIN_CUT_PCT
  expected_risk_cut_pct
                 corridor risk removed, as % of this corner's risk today
"""
from __future__ import annotations

import json
from dataclasses import fields, replace

import numpy as np

from ..agent_value import MENU
from ..sim import corridor as SIM
from ..sim.inputs import NodeInputs, Value
from ..sim.interventions import FUTURES, Changes, Future

VERSION = "triage-1"
DELAY_LIMIT_S = 8.0
MIN_CUT_PCT = 2.0
BANDS = ("low", "medium", "high", "very_high")
STREAMS = ("ave_left", "ave_right", "cross_turn", "ave_through_n", "ave_through_s",
           "cross_through_e", "cross_through_w")
FUTURE_WEIGHTS = {"today": 0.35, **{f: 0.13 for f in FUTURES if f != "today"}}


def change_key(m: dict) -> str:
    k, v = next(iter(m.items()))
    fmt = {"lpi_s": lambda: f"lpi_{v:g}", "turn_ban": lambda: f"ban_{v}", "cycle_s": lambda: f"cycle_{v:g}",
           "ave_share": lambda: f"ave_share_{round(v * 100)}"}
    return fmt[k]() if k in fmt else k


CHANGES = {change_key(m): m for m in MENU}
ACTIONS = tuple(CHANGES) + ("none",)
CHANGE_TEXT = {
    "lpi_4": "walk head start 4 s", "lpi_7": "walk head start 7 s", "lpi_10": "walk head start 10 s",
    "daylighting": "remove parking next to the crosswalks", "hardened_centerline": "slower left turns",
    "split_phase": "separate turn phase (turns never share the walk)",
    "ban_left": "ban left turns from 7 Av", "ban_right": "ban right turns from 7 Av",
    "ban_both": "ban all turns from 7 Av", "cycle_70": "70 s signal cycle", "cycle_110": "110 s signal cycle",
    "ave_share_50": "50% of green to 7 Av", "ave_share_68": "68% of green to 7 Av",
}


def turns(n: NodeInputs) -> list[str]:
    return [k for k, d in (("left", "EB"), ("right", "WB")) if d in n.cross_dirs]


def valid(key: str, n: NodeInputs) -> bool:
    ban = CHANGES[key].get("turn_ban")
    if ban in ("left", "right"):
        return ban in turns(n)
    return ban != "both" or bool(turns(n))


# ---- scenarios -------------------------------------------------------------
def _v(x, nd=3) -> Value:
    return Value(round(float(x), nd), "triage perturbation", "assumption")


def perturb(n: NodeInputs, rng: np.random.Generator) -> NodeInputs:
    """Walkers x0.4-2.5 (log-uniform), vehicles x0.6-1.4, LPI 0/4/7/10 s, widths +-20%."""
    ws, wa = rng.uniform(0.8, 1.2, size=2)
    cm = n.crossing_m
    return replace(
        n,
        ped_vph=_v(float(n.ped_vph) * np.exp(rng.uniform(np.log(0.4), np.log(2.5))), 0),
        ave_vph=_v(float(n.ave_vph) * rng.uniform(0.6, 1.4), 0),
        cross_vph={d: _v(float(v) * rng.uniform(0.6, 1.4), 0) for d, v in n.cross_vph.items()},
        ave_turn_share=_v(rng.uniform(0.05, 0.18)),
        cross_turn_share=_v(rng.uniform(0.15, 0.35)),
        crossing_m={"E": round(cm["E"] * ws, 1), "W": round(cm["W"] * ws, 1),
                    "N": round(cm["N"] * wa, 1), "S": round(cm["S"] * wa, 1)},
        lpi_s=_v(rng.choice([0, 4, 7, 10]), 0),
        cycle_s=_v(rng.choice([80, 90, 100]), 0),
        turn_speed_mps=_v(float(n.turn_speed_mps) * rng.uniform(0.85, 1.15), 2),
    )


def pick_future(rng: np.random.Generator) -> str:
    ids = list(FUTURE_WEIGHTS)
    return ids[rng.choice(len(ids), p=np.array(list(FUTURE_WEIGHTS.values())) / sum(FUTURE_WEIGHTS.values()))]


def future_effects(f: Future, node_id: str) -> dict:
    """The future's multipliers that differ from today, with walkers resolved for this corner."""
    out = {}
    for fl in fields(Future):
        if fl.name in ("id", "label", "why", "ped_mult", "ped_mult_nodes"):
            continue
        v = getattr(f, fl.name)
        if v != fl.default:
            out[fl.name] = v
    if f.ped_mult_for(node_id) != 1.0:
        out["walkers_mult"] = f.ped_mult_for(node_id)
    if "veh_mult" in out:
        out["vehicles_mult"] = out.pop("veh_mult")
    return out


def features(n: NodeInputs, future_id: str) -> dict:
    """What the model sees: the corner's inputs today plus the stress future's multipliers."""
    ave, share = float(n.ave_vph), float(n.ave_turn_share)
    cross = sum(float(v) for v in n.cross_vph.values())
    tk = turns(n)
    return {
        "walkers_ph": round(float(n.ped_vph)),
        "vehicles_ph": {"ave_through": round(ave * (1 - share * len(tk))),
                        "ave_left": round(ave * share) if "left" in tk else 0,
                        "ave_right": round(ave * share) if "right" in tk else 0,
                        "cross_through": round(cross * (1 - float(n.cross_turn_share))),
                        "cross_turn": round(cross * float(n.cross_turn_share))},
        "turns_from_7av": tk,
        "crossing_m": {"cross_street": n.crossing_m["E"], "avenue": n.crossing_m["N"]},
        "walk_head_start_s": float(n.lpi_s),
        "exclusive_ped_phase_s": float(n.excl_ped_s),
        "cycle_s": float(n.cycle_s),
        "ave_green_share": float(n.ave_share),
        "turn_speed_kmh": round(float(n.turn_speed_mps) * 3.6, 1),
        "walkers_against_signal_share": float(n.noncompliance),
        "future": {"id": future_id, **future_effects(FUTURES[future_id], n.id)},
    }


def label(inputs: list[NodeInputs], node_id: str, future_id: str) -> dict:
    """Simulate today and every valid single change at node_id; return the label and all options."""
    fut = FUTURES[future_id]
    n = next(x for x in inputs if x.id == node_id)
    today = SIM.simulate(inputs, {}, fut)
    nd = today["nodes"][node_id]
    veh = max(1, sum(s["vehicles"] for s in nd["by_stream"].values()))
    corner_risk = max(nd["sev_ph"], 1e-6)
    options = {}
    for key, m in CHANGES.items():
        if not valid(key, n):
            continue
        r = SIM.simulate(inputs, {node_id: Changes(**m)}, fut)
        cut = 100 * (today["totals"]["sev_ph"] - r["totals"]["sev_ph"]) / corner_risk
        delay = 3600 * (r["totals"]["car_delay_h"] - today["totals"]["car_delay_h"]) / veh
        options[key] = {"cut_pct": round(cut, 1), "delay_s_per_veh": round(delay, 2),
                        "feasible": delay <= DELAY_LIMIT_S}
    feas = {k: o for k, o in options.items() if o["feasible"]}
    best = max(feas, key=lambda k: feas[k]["cut_pct"]) if feas else None
    if best is None or feas[best]["cut_pct"] < MIN_CUT_PCT:
        best = "none"
    top = max(nd["by_stream"], key=lambda s: nd["by_stream"][s]["sev"])
    return {"near_miss_ph": round(nd["near_miss_ph"], 1), "sev_ph": nd["sev_ph"], "top_conflict": top,
            "best_single_change": best,
            "expected_risk_cut_pct": options[best]["cut_pct"] if best != "none" else 0.0,
            "options": options}


def band(near_miss_ph: float, cuts: list[float]) -> str:
    return BANDS[sum(near_miss_ph >= c for c in cuts)]


# ---- prompt ----------------------------------------------------------------
def system_prompt(cuts: list[float]) -> str:
    a, b, c = (f"{x:g}" for x in cuts)
    changes = "; ".join(f"{k} = {v}" for k, v in CHANGE_TEXT.items())
    return (
        "You triage one signalised intersection on 7th Avenue, Manhattan (7 Av runs one way southbound) "
        "for pedestrian near-miss risk in the 5-6 pm hour. The user sends JSON: walkers and vehicles per "
        "hour today, signal timing, geometry, and a stress 'future' whose multipliers change today's "
        "numbers (walkers_mult, vehicles_mult, detect_delta = change in drivers' chance of seeing a walker, "
        "and so on).\n"
        "Reply with only this JSON object:\n"
        '{"risk_band": "low|medium|high|very_high", "near_miss_ph": number, "top_conflict": stream, '
        '"best_single_change": change, "expected_risk_cut_pct": number}\n'
        f"near_miss_ph = simulated near-misses per hour at this corner under the future. risk_band: low < {a}, "
        f"medium {a}-{b}, high {b}-{c}, very_high >= {c} near-misses/h.\n"
        f"Streams: {', '.join(STREAMS)} (ave_* = 7 Av traffic, cross_* = cross-street traffic, "
        "through_n/s and through_e/w = the crosswalk they cross).\n"
        f"Changes: {changes}; none = no change cuts risk by at least {MIN_CUT_PCT:g}%. Only ban a turn that "
        "exists at the corner.\n"
        f"best_single_change = the one change that cuts risk the most while adding at most {DELAY_LIMIT_S:g} s "
        "of car delay per vehicle; risk pushed onto the next block counts against it. "
        "expected_risk_cut_pct = risk removed as % of this corner's risk (0 for none).\n\n"
        "Reasoning strength: low."
    )


def answer(row: dict) -> dict:
    return {k: row["label"][k] for k in ("risk_band", "near_miss_ph", "top_conflict", "best_single_change",
                                         "expected_risk_cut_pct")}


def messages(row: dict, cuts: list[float], shots: list[dict] = ()) -> list[dict]:
    msgs = [{"role": "system", "content": system_prompt(cuts)}]
    for s in shots:
        msgs += [{"role": "user", "content": json.dumps(s["input"], separators=(",", ":"))},
                 {"role": "assistant", "content": json.dumps(answer(s), separators=(",", ":"))}]
    msgs.append({"role": "user", "content": json.dumps(row["input"], separators=(",", ":"))})
    return msgs


def parse(text: str) -> dict | None:
    """First JSON object in text that has every field with an allowed value; else None."""
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, _ = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict) and _ok(obj):
            return obj
        i = text.find("{", i + 1)
    return None


def _ok(o: dict) -> bool:
    try:
        return (o["risk_band"] in BANDS and o["top_conflict"] in STREAMS and o["best_single_change"] in ACTIONS
                and isinstance(o["near_miss_ph"], (int, float)) and isinstance(o["expected_risk_cut_pct"], (int, float)))
    except KeyError:
        return False
