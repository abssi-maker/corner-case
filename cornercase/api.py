"""FastAPI app: serves the UI, recorded runs, and live runs streamed over SSE.

    uv run uvicorn cornercase.api:app --port 8000
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .meta import load_key
from .run import RUNS, Run

WEB = Path(__file__).resolve().parent / "web"
app = FastAPI(title="Corner Case")
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")

_jobs: dict[str, dict] = {}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/runs")
def runs():
    out = []
    for p in sorted(RUNS.glob("*.json"), reverse=True):
        try:
            with p.open() as f:
                head = json.load(f)
            out.append({"id": head["id"], "created": head.get("created"), "mode": head.get("mode"),
                        "cost_usd": head.get("cost_usd", {}).get("this_run"),
                        "corners": len(head.get("step5", {}).get("agents", {}))})
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    latest = (RUNS / "latest.txt").read_text().strip() if (RUNS / "latest.txt").exists() else None
    return {"runs": out, "latest": latest, "live_available": bool(load_key())}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    p = RUNS / f"{Path(run_id).name}.json"
    if not p.exists():
        raise HTTPException(404, "run not found")
    return FileResponse(p, media_type="application/json")


@app.post("/api/live")
def start_live():
    if not load_key():
        raise HTTPException(400, "META_API_KEY is not set; replay a recorded run instead")
    if any(j["status"] == "running" for j in _jobs.values()):
        raise HTTPException(409, "a live run is already in progress")
    job = uuid.uuid4().hex[:8]
    _jobs[job] = {"status": "running", "events": [], "run_id": None, "error": None}

    def work():
        def emit(ev):
            _jobs[job]["events"].append(ev)
        try:
            run = Run(emit=emit).live()
            _jobs[job]["run_id"] = run["id"]
            _jobs[job]["status"] = "done"
        except Exception as e:  # surfaced to the UI as a designed error state
            _jobs[job]["status"] = "error"
            _jobs[job]["error"] = str(e)[:400]
            emit({"type": "error", "msg": str(e)[:400]})

    threading.Thread(target=work, daemon=True).start()
    return {"job": job}


@app.get("/api/live/{job}/events")
def live_events(job: str):
    if job not in _jobs:
        raise HTTPException(404, "no such job")
    j = _jobs[job]

    def stream():
        idx, idle = 0, 0.0
        while True:
            evs = j["events"]
            while idx < len(evs):
                yield f"data: {json.dumps(evs[idx])}\n\n"
                idx += 1
                idle = 0.0
            if j["status"] != "running":
                yield f"data: {json.dumps({'type': 'end', 'status': j['status'], 'run_id': j['run_id'], 'error': j['error']})}\n\n"
                return
            time.sleep(0.3)
            idle += 0.3
            if idle > 15:
                idle = 0.0
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/health")
def health():
    return JSONResponse({"ok": True, "time": time.time()})
