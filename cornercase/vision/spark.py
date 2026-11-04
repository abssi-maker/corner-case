"""Muse Spark watches the corner for behaviour that boxes cannot show."""
from __future__ import annotations

import json

EVENT_KINDS = ["crossing_against_signal", "crossing_outside_crosswalk", "phone_while_crossing",
               "car_turning_through_walkers", "car_blocking_crosswalk", "driver_failed_to_yield",
               "crowd_spilling_into_roadway", "traffic_queue", "dense_crowd", "other"]

SCHEMA = {
    "name": "corner_watch",
    "schema": {
        "type": "object", "additionalProperties": False,
        "required": ["events", "signal_visible", "walkers_crossing_roadway", "walkers_crossing_against_signal",
                     "turning_vehicles", "vehicles_turning_with_walkers_present", "lighting", "summary"],
        "properties": {
            "events": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["t_s", "kind", "count", "where", "note"],
                "properties": {
                    "t_s": {"type": "number"},
                    "kind": {"type": "string", "enum": EVENT_KINDS},
                    "count": {"type": "integer"},
                    "where": {"type": "string"},
                    "note": {"type": "string"}}}},
            "signal_visible": {"type": "boolean"},
            "walkers_crossing_roadway": {"type": "integer"},
            "walkers_crossing_against_signal": {"type": "integer"},
            "turning_vehicles": {"type": "integer"},
            "vehicles_turning_with_walkers_present": {"type": "integer"},
            "lighting": {"type": "string", "enum": ["day", "dusk", "night"]},
            "summary": {"type": "string"},
        },
    },
}

PROMPT = """You are a street-safety analyst watching a short clip of a busy Manhattan corner
(7th Avenue / Broadway near W 45th St, Times Square). The camera is handheld and pans.

Each frame shows the video time in the top-left corner (t=SS.SS). Read every timestamp from that clock.

Report only what you can see. Do not guess what is off screen. If you cannot see the
pedestrian signal, say so (signal_visible=false) and only report crossing against the
signal when walkers step into moving traffic or cross while cars clearly have the green.

Events (one per distinct moment, with t_s from the clock):
- crossing_against_signal, crossing_outside_crosswalk, phone_while_crossing,
- car_turning_through_walkers (a turning vehicle passes close to people in the crosswalk),
- car_blocking_crosswalk, driver_failed_to_yield, crowd_spilling_into_roadway,
- traffic_queue (vehicles stopped or crawling), dense_crowd (crowding that affects walking), other.
Note every safety-relevant moment you can see, about one event every 2-3 seconds of video,
even when nothing dangerous happens (then use traffic_queue / dense_crowd / other).
count = how many people (or vehicles) are involved; where = short location in the frame
("left curb lane", "far crosswalk"); note = one plain sentence.

Totals: walkers_crossing_roadway = people you saw step into or cross the roadway;
walkers_crossing_against_signal = how many of those did so against the signal (0 if unknown);
turning_vehicles; vehicles_turning_with_walkers_present; lighting; a two-sentence summary."""


def watch(client, file_id: str) -> tuple[dict, dict]:
    body = client.respond("spark", [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": PROMPT},
        {"type": "input_video", "file_id": file_id}]}],
        stage="spark:watch", schema=SCHEMA, reasoning="medium", max_output_tokens=6000)
    from ..meta import output_text
    text = output_text(body)
    return json.loads(text), body
