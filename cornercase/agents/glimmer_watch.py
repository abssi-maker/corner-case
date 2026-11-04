"""After it's built: an always-on Muse Glimmer watcher.

Every minute the camera pipeline (SAM 3.1 tracks) is reduced to a small summary:
walkers, vehicles, and close calls (a walker and a vehicle box nearly touching while
the vehicle moves). Glimmer, an open-weights model served on Modal
(modal/glimmer_serve.py), reads those summaries and keeps the weekly near-miss log and
alerts. The close-call counter is deterministic; Glimmer writes the log, explains
spikes and decides when to alert.

Configure with GLIMMER_BASE_URL (OpenAI-compatible /v1) and GLIMMER_MODEL.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
TRACKS = ROOT / "data" / "vision" / "tracks.json"

SYSTEM = """You are the near-miss watcher for one Manhattan intersection where the city just installed a
safety change. You read per-interval camera summaries (counts from a tracker; close_calls are
moments a walker and a moving vehicle came within a car length). Keep a factual log. Never call a
close call an injury. Reply with JSON: {"log": [{"interval": str, "close_calls": int, "note": str}],
"total_close_calls": int, "alert": bool, "alert_text": str, "trend_note": str}."""


def _centre(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def close_calls(tracks: dict, gap_frac: float = 0.15) -> list[dict]:
    """Frames where a person box and a moving vehicle box are within gap_frac of frame width."""
    fps = tracks["fps"]
    W = tracks["width"]
    by_frame: dict[int, dict[str, list]] = {}
    for tr in tracks["tracks"]:
        kind = "person" if tr["label"] == "person" else "vehicle"
        prev = None
        for t, b in zip(tr["t"], tr["box"]):
            f = int(round(t * fps))
            moving = prev is not None and abs(_centre(b)[0] - _centre(prev)[0]) > 2
            by_frame.setdefault(f, {"person": [], "vehicle": []})[kind].append((b, moving))
            prev = b
    events = []
    for f, objs in sorted(by_frame.items()):
        for vb, mv in objs["vehicle"]:
            if not mv:
                continue
            for pb, _ in objs["person"]:
                dx = max(0, max(vb[0], pb[0]) - min(vb[2], pb[2]))
                dy = max(0, max(vb[1], pb[1]) - min(vb[3], pb[3]))
                if dx < gap_frac * W and dy < gap_frac * W * 0.5 and pb[3] > vb[1]:
                    events.append({"t": round(f / fps, 2)})
                    break
    # merge events closer than 1 s
    merged = []
    for e in events:
        if not merged or e["t"] - merged[-1]["t"] > 1.0:
            merged.append(e)
    return merged


def summaries(tracks: dict, interval_s: float = 5.0) -> list[dict]:
    fps = tracks["fps"]
    calls = close_calls(tracks)
    end = max((max(tr["t"]) for tr in tracks["tracks"] if tr["t"]), default=0)
    out = []
    t = 0.0
    while t <= end:
        people = {tr["id"] for tr in tracks["tracks"] if tr["label"] == "person" and any(t <= x < t + interval_s for x in tr["t"])}
        veh = {tr["id"] for tr in tracks["tracks"] if tr["label"] != "person" and any(t <= x < t + interval_s for x in tr["t"])}
        out.append({"interval": f"0:{int(t):02d}-0:{int(min(t + interval_s, end + 1 / fps)):02d}",
                    "walkers": len(people), "vehicles": len(veh),
                    "close_calls": sum(1 for c in calls if t <= c["t"] < t + interval_s)})
        t += interval_s
    return out


def configured() -> bool:
    return bool(os.environ.get("GLIMMER_BASE_URL"))


def watch(summary: list[dict], context: str) -> dict:
    """Ask Glimmer to write the log. Raises if the endpoint is not configured."""
    base = os.environ["GLIMMER_BASE_URL"].rstrip("/")
    model = os.environ.get("GLIMMER_MODEL", "muse-glimmer")
    headers = {"Authorization": f"Bearer {os.environ.get('GLIMMER_API_KEY', 'none')}"}
    payload = {"model": model, "temperature": 0.2, "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": context + "\nSummaries:\n" + json.dumps(summary)}]}
    t0 = time.time()
    r = requests.post(f"{base}/chat/completions", json=payload, headers=headers, timeout=600)
    r.raise_for_status()
    body = r.json()
    text = body["choices"][0]["message"]["content"]
    return {"result": json.loads(text), "model": body.get("model", model), "latency_s": round(time.time() - t0, 2),
            "usage": body.get("usage"), "endpoint": base}


def run_watcher(context: str) -> dict:
    tracks = json.loads(TRACKS.read_text())
    summ = summaries(tracks)
    out = {"summaries": summ, "close_calls_counted": sum(s["close_calls"] for s in summ),
           "counter": "deterministic: walker and moving vehicle boxes within 15% of frame width (SAM 3.1 tracks)"}
    if not configured():
        out["status"] = "not_deployed"
        out["how"] = "modal deploy modal/glimmer_serve.py, then set GLIMMER_BASE_URL and GLIMMER_MODEL"
        return out
    try:
        out.update(watch(summ, context))
        out["status"] = "live"
    except (requests.RequestException, KeyError, json.JSONDecodeError) as e:
        out["status"] = "error"
        out["error"] = str(e)[:300]
    return out
