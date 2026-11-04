"""Unit costs for each change, found by Muse Spark web search, with citations.

Results are cached in data/costs.json so replays and later runs reuse the same cited
figures; each entry records the model, date and source URLs.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..meta import MetaClient, citations, output_text

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "costs.json"

ITEMS = {
    "lpi": "adding a leading pedestrian interval (signal timing change) at an existing signalized intersection",
    "turn_ban": "a peak-hour turn restriction at an intersection (signs and enforcement)",
    "hardened_centerline": "a hardened centerline (rubber curbs and flexible delineator posts) at one intersection",
    "daylighting": "daylighting an intersection corner (removing parking near the crosswalk, with markings or "
                   "flexible posts)",
    "split_phase": "adding a protected turn phase / split phase to an existing signal (retiming, signal heads)",
    "retime": "retiming a traffic signal (cycle length and splits) at one intersection",
}

SCHEMA = {
    "name": "unit_cost",
    "schema": {"type": "object", "additionalProperties": False,
               "required": ["low_usd", "high_usd", "unit", "basis", "source_urls"],
               "properties": {"low_usd": {"type": "number"}, "high_usd": {"type": "number"},
                              "unit": {"type": "string"}, "basis": {"type": "string"},
                              "source_urls": {"type": "array", "items": {"type": "string"}}}},
}

PROMPT = """Find a typical installed cost in US dollars for {what}. Prefer NYC DOT, FHWA
(e.g. Proven Safety Countermeasures, CMF Clearinghouse) or other US DOT sources; use other
public agencies only if needed. Give a low-high range per intersection, the unit, a one-sentence
basis naming the source, and the URLs you used. If sources disagree, give the range they span."""


def change_kinds(changes: dict) -> list[str]:
    k = []
    if changes.get("lpi_s") is not None:
        k.append("lpi")
    if changes.get("turn_ban") not in (None, "none"):
        k.append("turn_ban")
    if changes.get("hardened_centerline"):
        k.append("hardened_centerline")
    if changes.get("daylighting"):
        k.append("daylighting")
    if changes.get("split_phase"):
        k.append("split_phase")
    if changes.get("cycle_s") is not None or changes.get("ave_share") is not None:
        k.append("retime")
    return k


def load_cache() -> dict:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def lookup(client: MetaClient | None, kinds: list[str], refresh: bool = False) -> dict:
    cache = load_cache()
    todo = [k for k in kinds if refresh or k not in cache]
    if todo and client is not None:
        def one(k):
            body = client.respond("spark", [{"role": "user", "content": PROMPT.format(what=ITEMS[k])}],
                                  stage=f"spark:cost:{k}", tools=[{"type": "web_search"}], schema=SCHEMA,
                                  reasoning="low", max_output_tokens=6000)
            data = json.loads(output_text(body))
            cites = citations(body)
            urls = list(dict.fromkeys([c["url"] for c in cites] + data.get("source_urls", [])))
            return k, {**data, "source_urls": urls[:5], "citations": cites[:5], "model": body.get("model"),
                       "searched_at": time.strftime("%Y-%m-%d"), "item": ITEMS[k]}
        with ThreadPoolExecutor(4) as ex:
            for k, v in ex.map(one, todo):
                cache[k] = v
        CACHE.write_text(json.dumps(cache, indent=1))
    return {k: cache[k] for k in kinds if k in cache}
