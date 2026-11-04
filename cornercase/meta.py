"""Meta Model API client with a call ledger.

Every call made through `MetaClient` appends one record (model, endpoint, latency,
usage as returned, retries, cost computed from published prices) to `self.calls`,
which the run file stores verbatim.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests

BASE = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")
ROOT = Path(__file__).resolve().parent.parent

# USD, from dev.meta.ai/models (read 2026-11-03/04).
PRICES = {
    "muse-wonder": {"in": 4.00e-6, "cached_in": 0.20e-6, "out": 20.00e-6},
    "muse-spark-1.3": {"in": 1.25e-6, "cached_in": 0.15e-6, "out": 4.25e-6},
    "sam-3.1": {"per_frame": 0.20e-3, "min_frames": 50, "per_image": 2.50e-3},
    "muse-image": {"per_image": 0.01},
}

# role -> ordered preferences; resolved against GET /models at startup
ROLE_PREFS = {
    "wonder": [r"^muse-wonder-[\d.]+$", r"^muse-wonder"],
    "spark": [r"^muse-spark-1\.3$", r"^muse-spark-[\d.]+$"],
    "sam": [r"^sam-3\.1$", r"^sam-"],
    "image": [r"^muse-image"],
}


def load_key() -> str | None:
    key = os.environ.get("META_API_KEY")
    if key:
        return key
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("META_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def price_for(model: str) -> dict | None:
    for k, v in PRICES.items():
        if model.startswith(k):
            return v
    return None


def token_cost(model: str, usage: dict) -> float | None:
    p = price_for(model)
    if not p or "in" not in p or not usage:
        return None
    inp = usage.get("input_tokens", 0) or 0
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    return round((inp - cached) * p["in"] + cached * p["cached_in"] + out * p["out"], 6)


def output_text(body: dict, final_only: bool = True) -> str:
    """Concatenate assistant output_text; skips 'commentary' messages when final_only."""
    parts = []
    for o in body.get("output", []):
        if o.get("type") != "message":
            continue
        if final_only and o.get("phase") == "commentary":
            continue
        parts += [c.get("text", "") for c in o.get("content", []) if c.get("type") == "output_text"]
    return "".join(parts)


def citations(body: dict) -> list[dict]:
    out, seen = [], set()
    for o in body.get("output", []):
        for c in o.get("content", []) or []:
            for a in c.get("annotations", []) or []:
                url = a.get("url")
                if url and url not in seen:
                    seen.add(url)
                    out.append({"url": url, "title": a.get("title", "")})
    return out


class MetaError(RuntimeError):
    pass


class MetaClient:
    def __init__(self, key: str | None = None, stage_prefix: str = ""):
        self.key = key or load_key()
        if not self.key:
            raise MetaError("META_API_KEY is not set (put it in .env); replay mode needs no key")
        self.h = {"Authorization": f"Bearer {self.key}"}
        self.calls: list[dict] = []
        self.models: dict[str, str] = {}
        self.stage_prefix = stage_prefix

    # ---- models ---------------------------------------------------------
    def resolve_models(self) -> dict[str, str]:
        r = requests.get(f"{BASE}/models", headers=self.h, timeout=30)
        r.raise_for_status()
        ids = [m["id"] for m in r.json().get("data", [])]
        resolved = {}
        for role, pats in ROLE_PREFS.items():
            for p in pats:
                hit = sorted(i for i in ids if re.search(p, i) and "contributor" not in i)
                if hit:
                    resolved[role] = hit[-1]
                    break
        self.models = resolved
        self.available = ids
        return resolved

    def model(self, role: str) -> str:
        if not self.models:
            self.resolve_models()
        if role not in self.models:
            raise MetaError(f"no model available for role {role!r}")
        return self.models[role]

    # ---- ledger ---------------------------------------------------------
    def _record(self, stage, model, endpoint, t0, usage, cost, retries=0, extra=None):
        rec = {"stage": self.stage_prefix + stage, "model": model, "endpoint": endpoint,
               "latency_s": round(time.time() - t0, 3), "usage": usage, "retries": retries,
               "cost_usd": cost, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if extra:
            rec.update(extra)
        self.calls.append(rec)
        return rec

    def _post(self, path, retries=2, **kw):
        last = None
        for attempt in range(retries + 1):
            try:
                r = requests.post(f"{BASE}{path}", headers={**self.h, **kw.pop("headers", {})}, **kw)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                return r, attempt
            except requests.RequestException as e:  # network blip: retry
                last = e
                time.sleep(2 * (attempt + 1))
        raise MetaError(f"POST {path} failed: {last}")

    # ---- files ----------------------------------------------------------
    def upload(self, path: Path, mime="video/mp4", stage="upload") -> str:
        t0 = time.time()
        with path.open("rb") as f:
            r, n = self._post("/files", data={"purpose": "user_data"},
                              files={"file": (path.name, f, mime)}, timeout=300)
        if r.status_code >= 400:
            raise MetaError(f"upload: HTTP {r.status_code}: {r.text[:300]}")
        body = r.json()
        self._record(stage, "-", "POST /files", t0, {"bytes": body.get("bytes")}, 0.0, n)
        return body["id"]

    # ---- responses ------------------------------------------------------
    def respond(self, role: str, input, *, stage: str, tools=None, schema=None,
                previous_response_id=None, reasoning=None, max_output_tokens=None,
                instructions=None, timeout=180) -> dict:
        model = self.model(role)
        payload = {"model": model, "input": input}
        if tools:
            payload["tools"] = tools
        if schema:
            payload["text"] = {"format": {"type": "json_schema", "name": schema["name"],
                                          "schema": schema["schema"], "strict": True}}
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        if reasoning:
            payload["reasoning"] = {"effort": reasoning}
        if max_output_tokens:
            payload["max_output_tokens"] = max_output_tokens
        if instructions:
            payload["instructions"] = instructions
        t0 = time.time()
        r, n = self._post("/responses", retries=1, json=payload, timeout=(15, timeout))
        body = r.json() if "json" in r.headers.get("content-type", "") else {"error": r.text}
        if r.status_code >= 400:
            self._record(stage, model, "POST /responses", t0, {}, None, n, {"error": str(body)[:300]})
            raise MetaError(f"{stage}: HTTP {r.status_code}: {str(body)[:300]}")
        usage = body.get("usage") or {}
        tool_kinds = [o.get("type") for o in body.get("output", []) if o.get("type", "").endswith("_call")]
        self._record(stage, model, "POST /responses", t0, usage, token_cost(model, usage), n,
                     {"status": body.get("status"), "tool_calls": tool_kinds})
        return body

    # ---- SAM 3.1 (streamed text tokens) ----------------------------------
    # Hosted SAM 3.1, per dev.meta.ai/docs/sam: one concept noun phrase per request (or
    # box/point prompts), video tracks at most 16 objects per frame, image requests return
    # every instance. metadata values are strings.
    SAM_META = {"include_confidence": "true", "mask_encoding": "one_bit"}

    def sam(self, prompt: str, *, stage: str, file_id: str | None = None, image_url: str | None = None,
            metadata: dict | None = None, deadline_s=240, retries=2) -> str:
        model = self.model("sam")
        media = ({"type": "input_video", "file_id": file_id} if file_id
                 else {"type": "input_image", "image_url": image_url})
        payload = {"model": model, "stream": True, "metadata": {**self.SAM_META, **(metadata or {})},
                   "input": [{"type": "message", "role": "user",
                              "content": [{"type": "input_text", "text": prompt}, media]}]}
        last = None
        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                r = requests.post(f"{BASE}/responses", json=payload, stream=True, timeout=(15, 90),
                                  headers={**self.h, "Accept": "text/event-stream"})
                if r.status_code == 400:  # permanent for this request (docs): don't retry
                    raise MetaError(f"{stage}: HTTP 400: {r.text[:300]}")
                if r.status_code >= 400:
                    raise requests.RequestException(f"HTTP {r.status_code}: {r.text[:200]}")
                text, done = [], None
                for line in r.iter_lines(decode_unicode=True):
                    if time.time() - t0 > deadline_s:
                        r.close()
                        raise requests.RequestException(f"no completion in {deadline_s}s")
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    ev = json.loads(data)
                    if ev.get("type") == "response.output_text.delta":
                        text.append(ev["delta"])
                    elif ev.get("type") in ("response.completed", "response.failed", "response.incomplete"):
                        done = ev
                usage = ((done or {}).get("response") or {}).get("usage") or {}
                p = PRICES["sam-3.1"]
                if file_id:
                    frames = usage.get("video_frames_processed")
                    cost = round(max(frames, p["min_frames"]) * p["per_frame"], 6) if frames else None
                    basis = "frames x published per-frame price"
                else:
                    cost, basis = p["per_image"], "published per-image price"
                self._record(stage, model, "POST /responses (stream)", t0, usage, cost, attempt,
                             {"prompt": prompt[:120], "cost_basis": basis})
                return "".join(text)
            except requests.RequestException as e:
                last = e
                time.sleep(0.5 * 2 ** attempt)
        self._record(stage, model, "POST /responses (stream)", time.time(), {}, None, retries,
                     {"error": str(last)[:200]})
        raise MetaError(f"{stage}: {last}")

    def sam_video(self, file_id: str, prompt: str, *, stage: str, **kw) -> str:
        return self.sam(prompt, stage=stage, file_id=file_id, **kw)

    def total_cost(self) -> float:
        return round(sum(c.get("cost_usd") or 0 for c in self.calls), 4)
