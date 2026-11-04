"""First screen: the corner-triage LoRA on Muse Glimmer reads all 30 corners in seconds.

The adapter (modal/glimmer_lora_train.py) learned one skill from the simulator: read one
intersection's inputs and return its risk band, near-miss rate, main conflict and the single
change that cuts risk most. Here it screens the real corridor before the Wonder agents start:
its guesses go into the coordinator's table (to set each agent's test budget) and into each
agent's brief as a first guess to beat. After a fix is built it is cheap enough (~2 s, a fraction
of a cent per corner) to re-read every corner every week.

Each guess is scored against the simulator's own answer for that corner (cornercase.finetune.
triage.label), so the run records how often the LoRA was right on this corridor.
Configure with GLIMMER_BASE_URL and GLIMMER_TRIAGE_MODEL (default corner-triage).
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from ..finetune import triage as T

ROOT = Path(__file__).resolve().parents[2]
FINETUNE = ROOT / "evidence" / "finetune.json"


def configured() -> bool:
    return bool(os.environ.get("GLIMMER_BASE_URL"))


def _cuts() -> list[float]:
    return json.loads(FINETUNE.read_text())["dataset"]["risk_band_cuts_near_miss_ph"]


def _call(base: str, model: str, msgs: list[dict]) -> dict:
    t0 = time.time()
    try:
        r = requests.post(f"{base}/chat/completions", timeout=600, json={
            "model": model, "messages": msgs, "temperature": 0, "max_tokens": 1024})
        r.raise_for_status()
        b = r.json()
        text = b["choices"][0]["message"].get("content") or ""
        return {"pred": T.parse(text), "usage": b.get("usage") or {}, "latency_s": round(time.time() - t0, 2),
                "error": None}
    except (requests.RequestException, KeyError, ValueError) as e:
        return {"pred": None, "usage": {}, "latency_s": round(time.time() - t0, 2), "error": str(e)[:200]}


def triage_corridor(inputs, future_id: str = "today", workers: int = 8) -> dict:
    """Ask the LoRA about every corner, then score each answer against the simulator."""
    if not configured():
        return {"status": "not_deployed", "how": "set GLIMMER_BASE_URL (see modal/README.md)"}
    base = os.environ["GLIMMER_BASE_URL"].rstrip("/")
    model = os.environ.get("GLIMMER_TRIAGE_MODEL", "corner-triage")
    cuts = _cuts()
    msgs = {n.id: T.messages({"input": T.features(n, future_id)}, cuts) for n in inputs}
    warm = _call(base, model, next(iter(msgs.values())))  # the first call after idle waits for a cold start
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        outs = dict(zip(msgs, ex.map(lambda m: _call(base, model, m), msgs.values())))
    wall = round(time.time() - t0, 1)
    corners, right_change, right_band, abs_err = {}, 0, 0, []
    for n in inputs:
        o = outs[n.id]
        lab = T.label(inputs, n.id, future_id)
        sim = {"risk_band": T.band(lab["near_miss_ph"], cuts), "near_miss_ph": lab["near_miss_ph"],
               "top_conflict": lab["top_conflict"], "best_single_change": lab["best_single_change"],
               "expected_risk_cut_pct": lab["expected_risk_cut_pct"]}
        p = o["pred"]
        if p:
            right_change += p["best_single_change"] == sim["best_single_change"]
            right_band += p["risk_band"] == sim["risk_band"]
            abs_err.append(abs(p["near_miss_ph"] - sim["near_miss_ph"]))
        corners[n.id] = {"glimmer": p, "simulator": sim, "latency_s": o["latency_s"], "error": o["error"],
                         "first_guess": T.CHANGE_TEXT.get(p["best_single_change"], "no change")
                         if p else None}
    ok = sum(1 for c in corners.values() if c["glimmer"])
    lat = sorted(o["latency_s"] for o in outs.values())
    return {
        "status": "live", "model": model, "endpoint": base, "future": future_id,
        "corners": corners, "wall_s": wall, "cold_start_s": warm["latency_s"],
        "latency_p50_s": lat[len(lat) // 2] if lat else None,
        "tokens": sum((o["usage"].get("total_tokens") or 0) for o in outs.values()),
        "score_vs_simulator": {"corners": len(corners), "valid": ok,
                               "best_change_matches": right_change, "risk_band_matches": right_band,
                               "near_miss_mae": round(sum(abs_err) / len(abs_err), 1) if abs_err else None},
        "note": "The LoRA learned the simulator, not real crashes; its guesses are a first screen, and "
                "the Wonder agents test every fix in the simulator before anything is chosen.",
    }
