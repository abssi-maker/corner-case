"""Build the corner-triage dataset: perturbed real corners, labelled by the simulator on Modal CPUs.

    uv run --with modal modal run -m cornercase.finetune.make_dataset
    -> data/finetune/{train,val,test}.jsonl + manifest.json

Split by corner: 6 of the 30 corners (every 5th by street) never appear in train or val,
so the test measures generalisation to unseen corners, not memorisation.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import modal
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "finetune"
SEED = 20261104
N_TRAINVAL, N_VAL, N_TEST_PER_CORNER = 3300, 300, 50
CHUNK = 60
BAND_QUANTILES = (0.25, 0.6, 0.9)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("numpy>=2.0", "pydantic>=2.8", "requests>=2.32")
    .add_local_file(ROOT / "data" / "corridor.json", "/root/data/corridor.json")  # sim reads ROOT/data
    .add_local_file(ROOT / "data" / "vision" / "measurements.json", "/root/data/vision/measurements.json")
    .add_local_python_source("cornercase")
)
app = modal.App("corner-case-triage-data", image=image)


@app.function(cpu=1.0, memory=1024, timeout=30 * 60)
def label_chunk(specs: list[dict]) -> list[dict]:
    from cornercase.finetune import triage as T
    from cornercase.sim.inputs import build_inputs

    real = build_inputs()
    out = []
    for s in specs:
        rng = np.random.default_rng([SEED, s["i"]])
        n = next(x for x in real if x.id == s["corner"])
        p = T.perturb(n, rng)
        fut = T.pick_future(rng)
        inputs = [p if x.id == n.id else x for x in real]
        out.append({"i": s["i"], "split": s["split"], "corner": n.id, "corner_name": n.name, "future": fut,
                    "input": T.features(p, fut), "label": T.label(inputs, n.id, fut)})
    return out


def _source_hash() -> str:
    h = hashlib.sha256()
    for f in sorted((ROOT / "cornercase" / "sim").glob("*.py")) + [ROOT / "cornercase" / "agent_value.py",
                                                                   ROOT / "cornercase" / "finetune" / "triage.py"]:
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


@app.local_entrypoint()
def main(limit: int = 0):
    from cornercase.finetune import triage as T
    from cornercase.sim.inputs import build_inputs

    corners = sorted(build_inputs(), key=lambda n: n.street_num)
    test_ids = [n.id for n in corners[2::5]]
    train_ids = [n.id for n in corners if n.id not in test_ids]
    specs = [{"i": i, "corner": train_ids[i % len(train_ids)], "split": "trainval"} for i in range(N_TRAINVAL)]
    specs += [{"i": N_TRAINVAL + k * N_TEST_PER_CORNER + j, "corner": c, "split": "test"}
              for k, c in enumerate(test_ids) for j in range(N_TEST_PER_CORNER)]
    if limit:
        specs = specs[:limit // 2] + specs[-(limit - limit // 2):]
    t0 = time.time()
    rows = [r for chunk in label_chunk.map([specs[i:i + CHUNK] for i in range(0, len(specs), CHUNK)])
            for r in chunk]
    wall = time.time() - t0

    rng = np.random.default_rng(SEED)
    tv = [r for r in rows if r["split"] == "trainval"]
    val_i = set(rng.choice([r["i"] for r in tv], size=min(N_VAL, len(tv) // 10), replace=False).tolist())
    for r in tv:
        r["split"] = "val" if r["i"] in val_i else "train"
    train = [r for r in rows if r["split"] == "train"]
    cuts = [float(round(np.quantile([r["label"]["near_miss_ph"] for r in train], q))) for q in BAND_QUANTILES]
    for r in rows:
        r["label"]["risk_band"] = T.band(r["label"]["near_miss_ph"], cuts)
        r["messages"] = T.messages(r, cuts) + [{"role": "assistant",
                                               "content": json.dumps(T.answer(r), separators=(",", ":"))}]

    OUT.mkdir(parents=True, exist_ok=True)
    counts = {}
    for split in ("train", "val", "test"):
        part = [r for r in rows if r["split"] == split]
        counts[split] = len(part)
        with (OUT / f"{split}.jsonl").open("w") as f:
            for r in part:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")

    def dist(key, part):
        vals, n = np.unique([r["label"][key] for r in part], return_counts=True)
        return dict(sorted(zip(vals.tolist(), n.tolist()), key=lambda x: -x[1]))

    manifest = {
        "generator": T.VERSION, "seed": SEED, "source_sha256_16": _source_hash(),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "modal_wall_s": round(wall, 1),
        "counts": counts, "test_corners": test_ids, "train_corners": train_ids,
        "risk_band_cuts_near_miss_ph": cuts, "band_quantiles_of_train": list(BAND_QUANTILES),
        "delay_limit_s_per_veh": T.DELAY_LIMIT_S, "min_cut_pct": T.MIN_CUT_PCT,
        "future_weights": T.FUTURE_WEIGHTS, "actions": list(T.ACTIONS), "streams": list(T.STREAMS),
        "perturbation": "walkers x0.4-2.5 log-uniform; avenue and each cross street x0.6-1.4; 7 Av turn share "
                        "0.05-0.18; cross turn share 0.15-0.35; LPI 0/4/7/10 s; crossing widths +-20%; cycle "
                        "80/90/100 s; turn speed x0.85-1.15. Other corners stay at their real inputs.",
        "label_rule": "best_single_change maximises corridor sev_ph removed (as % of the corner's sev_ph) among "
                      "valid MENU changes adding <= delay limit s/vehicle at the corner; 'none' if the best cut "
                      "< min_cut_pct. Simulated near-misses, not injuries.",
        "label_distribution": {s: {k: dist(k, [r for r in rows if r["split"] == s])
                                   for k in ("best_single_change", "risk_band", "top_conflict")}
                               for s in ("train", "test")},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps({k: manifest[k] for k in ("counts", "modal_wall_s", "risk_band_cuts_near_miss_ph",
                                               "test_corners")}, indent=1))
    print("train best_single_change:", manifest["label_distribution"]["train"]["best_single_change"])
