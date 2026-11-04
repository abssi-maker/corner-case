"""Base Muse Glimmer (3-shot) vs the corner-triage LoRA on the 300 held-out-corner test scenarios.

    GLIMMER_BASE_URL=https://.../v1 uv run python modal/glimmer_lora_eval.py
    -> data/finetune/eval.json (merged into evidence/finetune.json by the hand-off)

Only an HTTP client: both models run on the Modal endpoint (modal/glimmer_serve.py).
Same system prompt for both; the base model also gets 3 solved training examples. Also
scored: a trivial baseline (the most common training answer for every field).
Cost per call = H100 $/s x wall time / calls at the same concurrency (an estimate; the
real spend comes from `modal billing report`).
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cornercase.finetune import triage as T  # noqa: E402

DATA = ROOT / "data" / "finetune"
H100_USD_S = 0.001097  # modal.com/pricing, read 2026-11-04
CONCURRENCY = 16
SHOT_CHANGES = ("daylighting", "lpi_10", "cycle_70")


def load(split):
    return [json.loads(l) for l in (DATA / f"{split}.jsonl").open()]


def call(base, model, msgs, max_tokens=1024):
    t0 = time.time()
    try:
        r = requests.post(f"{base}/chat/completions", timeout=600, json={
            "model": model, "messages": msgs, "temperature": 0, "max_tokens": max_tokens})
        r.raise_for_status()
        b = r.json()
        m = b["choices"][0]["message"]
        return {"content": m.get("content") or "", "reasoning": m.get("reasoning_content") or m.get("reasoning") or "",
                "usage": b.get("usage") or {}, "latency_s": round(time.time() - t0, 3), "error": None}
    except (requests.RequestException, KeyError, ValueError) as e:
        return {"content": "", "reasoning": "", "usage": {}, "latency_s": round(time.time() - t0, 3), "error": str(e)[:200]}


def run_model(base, model, rows, cuts, shots):
    call(base, model, T.messages(rows[0], cuts, shots), 16)  # warm (cold start not counted)
    t0 = time.time()
    with ThreadPoolExecutor(CONCURRENCY) as ex:
        outs = list(ex.map(lambda r: call(base, model, T.messages(r, cuts, shots)), rows))
    wall = time.time() - t0
    for o in outs:
        o["pred"] = T.parse(o["content"])
    return outs, wall


def captured(row, pred):
    """Risk cut (% of the corner's risk) the predicted change really achieves in the simulator."""
    if not pred or pred["best_single_change"] == "none":
        return 0.0
    o = row["label"]["options"].get(pred["best_single_change"])
    return o["cut_pct"] if o and o["feasible"] else 0.0


def score(rows, preds):
    n = len(rows)
    valid = [p is not None for p in preds]
    hit = lambda k: np.array([bool(p) and p[k] == r["label"][k] for r, p in zip(rows, preds)], dtype=float)
    err = [abs(p["near_miss_ph"] - r["label"]["near_miss_ph"]) for r, p in zip(rows, preds) if p]
    cap = np.array([captured(r, p) for r, p in zip(rows, preds)])
    oracle = np.array([r["label"]["expected_risk_cut_pct"] for r in rows])
    return {"n": n, "json_valid_rate": round(sum(valid) / n, 3),
            "best_change_acc": round(hit("best_single_change").mean(), 3),
            "risk_band_acc": round(hit("risk_band").mean(), 3),
            "top_conflict_acc": round(hit("top_conflict").mean(), 3),
            "near_miss_ph_mae": round(float(np.mean(err)), 1) if err else None, "near_miss_mae_n": len(err),
            "risk_cut_captured_pct_of_oracle": round(100 * cap.sum() / max(oracle.sum(), 1e-9), 1),
            "_best_hits": hit("best_single_change"), "_band_hits": hit("risk_band")}


def boot_diff(a, b, n=10000, seed=0):
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), size=(n, len(a)))
    d = a[idx].mean(1) - b[idx].mean(1)
    return {"diff": round(float(a.mean() - b.mean()), 3),
            "ci95": [round(float(np.percentile(d, 2.5)), 3), round(float(np.percentile(d, 97.5)), 3)]}


def boot_diff_by_corner(a, b, corners, n=10000, seed=0):
    """Resample whole test corners (6 clusters), so the CI does not treat 50 scenarios at one
    corner as 50 independent corners."""
    rng = np.random.default_rng(seed)
    groups = [np.flatnonzero(np.array(corners) == c) for c in sorted(set(corners))]
    d = []
    for _ in range(n):
        idx = np.concatenate([groups[i] for i in rng.integers(0, len(groups), size=len(groups))])
        d.append(a[idx].mean() - b[idx].mean())
    return {"diff": round(float(a.mean() - b.mean()), 3),
            "ci95": [round(float(np.percentile(d, 2.5)), 3), round(float(np.percentile(d, 97.5)), 3)]}


def perf(outs, wall):
    lat = [o["latency_s"] for o in outs if not o["error"]]
    toks = [o["usage"].get("completion_tokens", 0) for o in outs]
    return {"errors": sum(bool(o["error"]) for o in outs), "wall_s": round(wall, 1), "concurrency": CONCURRENCY,
            "latency_p50_s": round(statistics.median(lat), 2) if lat else None,
            "latency_p95_s": round(float(np.percentile(lat, 95)), 2) if lat else None,
            "completion_tokens_mean": round(statistics.mean(toks), 1),
            "est_cost_per_call_usd": round(H100_USD_S * wall / len(outs), 5)}


def main():
    base = os.environ["GLIMMER_BASE_URL"].rstrip("/")
    base_model = os.environ.get("GLIMMER_MODEL", "muse-glimmer")
    triage_model = os.environ.get("GLIMMER_TRIAGE_MODEL", "corner-triage")
    man = json.loads((DATA / "manifest.json").read_text())
    cuts = man["risk_band_cuts_near_miss_ph"]
    train, test = load("train"), load("test")
    shots = [next(r for r in train if r["label"]["best_single_change"] == c) for c in SHOT_CHANGES]

    common = {k: Counter(r["label"][k] for r in train).most_common(1)[0][0]
              for k in ("best_single_change", "risk_band", "top_conflict")}
    mean_nm = round(float(np.mean([r["label"]["near_miss_ph"] for r in train])), 1)
    trivial = [{**common, "near_miss_ph": mean_nm, "expected_risk_cut_pct": 0.0} for _ in test]

    runs = {}
    for name, model, sh in (("base_3shot", base_model, shots), ("lora", triage_model, [])):
        outs, wall = run_model(base, model, test, cuts, sh)
        runs[name] = {"model": model, "outs": outs, "wall": wall}
        print(name, "done in", round(wall), "s")

    s = {k: score(test, [o["pred"] for o in v["outs"]]) for k, v in runs.items()}
    s["trivial_baseline"] = score(test, trivial)
    table = {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in s.items()}
    for k, v in runs.items():
        table[k].update(perf(v["outs"], v["wall"]))
    table["trivial_baseline"]["rule"] = {**common, "near_miss_ph": mean_nm}
    comparisons = {
        "best_change_acc lora - base_3shot": boot_diff(s["lora"]["_best_hits"], s["base_3shot"]["_best_hits"]),
        "best_change_acc lora - trivial": boot_diff(s["lora"]["_best_hits"], s["trivial_baseline"]["_best_hits"]),
        "best_change_acc base_3shot - trivial": boot_diff(s["base_3shot"]["_best_hits"], s["trivial_baseline"]["_best_hits"]),
        "risk_band_acc lora - base_3shot": boot_diff(s["lora"]["_band_hits"], s["base_3shot"]["_band_hits"]),
    }
    corners = [r["corner"] for r in test]
    by_corner_ci = {
        "best_change_acc lora - base_3shot": boot_diff_by_corner(s["lora"]["_best_hits"], s["base_3shot"]["_best_hits"], corners),
        "best_change_acc lora - trivial": boot_diff_by_corner(s["lora"]["_best_hits"], s["trivial_baseline"]["_best_hits"], corners),
    }
    per_corner = {c: {k: round(float(s[k]["_best_hits"][np.array(corners) == c].mean()), 2)
                      for k in ("lora", "base_3shot", "trivial_baseline")} for c in sorted(set(corners))}
    examples = {k: [{"corner": test[i]["corner"], "future": test[i]["future"], "input": test[i]["input"],
                     "label": T.answer(test[i]), "output": v["outs"][i]["content"][:600],
                     "reasoning_chars": len(v["outs"][i]["reasoning"])} for i in (0, 120, 240)]
                for k, v in runs.items()}
    out = {"endpoint": base, "models": {"base": base_model, "lora": triage_model},
           "test": {"n": len(test), "corners": man["test_corners"], "note": "corners never seen in training"},
           "prompt": {"system_chars": len(T.system_prompt(cuts)), "base_shots": [r["i"] for r in shots],
                      "temperature": 0, "max_tokens": 1024},
           "table": table, "bootstrap_95ci_paired_by_scenario": comparisons,
           "bootstrap_95ci_resampling_corners": by_corner_ci, "best_change_acc_per_test_corner": per_corner,
           "examples": examples,
           "raw": {k: [{"i": r["i"], "pred": o["pred"], "latency_s": o["latency_s"], "error": o["error"]}
                       for r, o in zip(test, v["outs"])] for k, v in runs.items()}}
    (DATA / "eval.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({"table": table, "ci": comparisons}, indent=1))


if __name__ == "__main__":
    main()
