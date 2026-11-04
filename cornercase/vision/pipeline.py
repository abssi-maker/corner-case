"""Step 2: watch the corner with SAM 3.1 and Muse Spark.

How SAM 3.1 is used (following dev.meta.ai/docs/sam):
  1. Detect, don't guess: on a keyframe every second, image-mode concept requests
     ("person", "car", "bus", "yellow taxi", "road") return EVERY instance with a confidence
     (video mode is capped at 16 objects per frame; Times Square has more than that).
  2. Track what was detected: new detections (not already covered by a track) become box
     prompts on their keyframe, sent in batches of <=16 objects, propagated forward. Each
     object gets one global id, so nobody is double counted and late arrivals are caught.
  3. Read the masks, not just boxes: one-bit masks give each walker's ground-contact point
     and the road surface itself, so "standing in the roadway" is measured, not inferred.
  4. Scene questions go to Spark, concepts go to SAM: Muse Spark watches the clip (clock burned
     in), writes timestamped notes and proposes extra concepts worth segmenting; SAM segments
     them; every Spark note is checked against SAM tracks.

    uv run python -m cornercase.vision.pipeline            # live (~$0.6 of API)
    uv run python -m cornercase.vision.pipeline --offline  # rebuild from saved raw responses
"""
from __future__ import annotations

import base64
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from ..meta import MetaClient, output_text
from . import homography as HG
from . import sam as SAM
from . import spark as SP
from . import video as V

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "footage" / "timessquare-2017.webm"
OUT = ROOT / "data" / "vision"
RAW = OUT / "sam_raw"
MEDIA = ROOT / "cornercase" / "web" / "static" / "media"
CAL = OUT / "calibration.json"

FPS = 6
W, H = 540, 960
KF_EVERY = 6                     # keyframe every second
TRACK_LABELS = {"person": "person", "car": "car", "bus": "bus"}
SCENE = {"taxi": "yellow taxi", "road": "asphalt road"}  # "road" also matched plaza paving
CONF_MIN = {"person": 0.5, "car": 0.5, "bus": 0.6, "taxi": 0.5, "road": 0.4}
BATCH = 16                       # hosted video limit: 16 objects per request
TAXI_LEN_M = 4.9
PERSON_H_M = 1.7


# ---------------------------------------------------------------- frames
def keyframe_jpegs(clip: Path, n_frames: int) -> dict[int, str]:
    """{frame index: data URL} for every keyframe of the 6 fps clip."""
    d = OUT / "keyframes"
    d.mkdir(exist_ok=True)
    out = {}
    for f in range(0, n_frames, KF_EVERY):
        p = d / f"kf_{f:03d}.jpg"
        if not p.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vf", f"select=eq(n\\,{f})",
                            "-frames:v", "1", "-q:v", "3", str(p)], check=True)
        out[f] = "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()
    return out


# ---------------------------------------------------------------- SAM calls
def detect_keyframes(client: MetaClient, kfs: dict[int, str]) -> dict[str, dict[int, list[SAM.Det]]]:
    concepts = {**TRACK_LABELS, **SCENE}
    jobs = [(label, phrase, f) for label, phrase in concepts.items() for f in kfs]

    def one(job):
        label, phrase, f = job
        txt = client.sam(phrase, image_url=kfs[f], stage=f"sam:image:{label}@{f}")
        (RAW / f"img_{label}_{f:03d}.txt").write_text(txt)
        return job, txt

    with ThreadPoolExecutor(12) as ex:
        res = list(ex.map(one, jobs))
    return _index_images(res)


def _index_images(res) -> dict[str, dict[int, list[SAM.Det]]]:
    out: dict[str, dict[int, list[SAM.Det]]] = {}
    for (label, _, f), txt in res:
        dets = SAM.parse_frames(txt).get(0, [])
        out.setdefault(label, {})[f] = [d for d in dets if (d.conf or 1) >= CONF_MIN[label]]
    return out


def load_images() -> dict[str, dict[int, list[SAM.Det]]]:
    res = []
    for p in sorted(RAW.glob("img_*.txt")):
        _, label, f = p.stem.split("_")
        res.append(((label, None, int(f)), p.read_text()))
    return _index_images(res)


def _covered(det: SAM.Det, tracks: list[SAM.Track], f: int) -> bool:
    for t in tracks:
        for ff in (f, f - 1, f + 1):
            d = t.at(ff)
            if d is not None and SAM.iou(d.box, det.box) > 0.3:
                return True
    return False


def track(client: MetaClient | None, file_id: str | None, images, n_frames: int):
    """Seed box-prompt tracks from keyframe detections, round by round (live), or replay
    the saved requests (offline). Returns ({label: [Track]}, manifest)."""
    from meta_sam_parser import FramePrompt, PromptBox, PromptObject, build_object_prompt

    manifest_path = RAW / "manifest.json"
    if client is None:
        manifest = json.loads(manifest_path.read_text())
        tracks: dict[str, list[SAM.Track]] = {k: [] for k in TRACK_LABELS}
        for req in manifest["requests"]:
            txt = (RAW / req["file"]).read_text()
            tracks[req["label"]] += SAM.tracks_from_text(txt, req["label"], min_frames=2, id_offset=req["id_offset"])
        return tracks, manifest

    tracks = {k: [] for k in TRACK_LABELS}
    manifest = {"requests": [], "conf_min": CONF_MIN, "batch": BATCH, "keyframe_every": KF_EVERY}
    next_offset = 1000
    for f in range(0, n_frames, KF_EVERY):
        reqs = []
        for label in TRACK_LABELS:
            new = [d for d in images.get(label, {}).get(f, []) if not _covered(d, tracks[label], f)]
            for b in range(0, len(new), BATCH):
                reqs.append((label, new[b:b + BATCH], next_offset))
                next_offset += 1000

        def one(req, f=f):
            label, dets, off = req
            objs = [PromptObject(id=i + 1, prompts=[FramePrompt(frame=f, box=PromptBox(
                left=d.box[0], top=d.box[1], right=d.box[2] - 1, bottom=d.box[3] - 1), points=())])
                for i, d in enumerate(dets)]
            prompt = build_object_prompt(size=(W, H), objects=objs)
            txt = client.sam(prompt, file_id=file_id, stage=f"sam:track:{label}@{f}",
                             metadata={"propagation_direction": "forward"})
            name = f"vid_{label}_{f:03d}_{off}.txt"
            (RAW / name).write_text(txt)
            return label, off, name, txt, len(dets)

        with ThreadPoolExecutor(8) as ex:
            for label, off, name, txt, n in ex.map(one, reqs):
                new_tracks = SAM.tracks_from_text(txt, label, min_frames=2, id_offset=off)
                tracks[label] += new_tracks
                manifest["requests"].append({"label": label, "seed_frame": f, "seeds": n, "file": name,
                                             "id_offset": off, "tracks": len(new_tracks)})
    manifest_path.write_text(json.dumps(manifest, indent=1))
    return tracks, manifest


# ---------------------------------------------------------------- measurement
def on_billboard(tr: SAM.Track) -> bool:
    """'person' also matches faces on Times Square billboards: big boxes high in the frame.
    Real walkers stand below y=700 in this portrait view."""
    b = np.array(tr.boxes)
    return bool(np.median(b[:, 3]) < 700 and np.median(b[:, 3] - b[:, 1]) > 90)


def road_masks(images) -> dict[int, np.ndarray]:
    out = {}
    for f, dets in images.get("road", {}).items():
        m = np.zeros((H, W), bool)
        for d in dets:
            r = d.raster()
            if r is None:
                continue
            x1, y1 = d.box[0], d.box[1]
            hh, ww = min(r.shape[0], H - y1), min(r.shape[1], W - x1)
            m[y1:y1 + hh, x1:x1 + ww] |= r[:hh, :ww].astype(bool)
        out[f] = m
    return out


def taxi_ids(cars: list[SAM.Track], images) -> set[int]:
    ids = set()
    for f, dets in images.get("taxi", {}).items():
        for t in cars:
            d = t.at(f)
            if d is not None and any(SAM.iou(d.box, x.box) > 0.5 for x in dets):
                ids.add(t.id)
    return ids


def measure(tracks, images, offsets, H_img2m) -> tuple[dict, dict]:
    people = [t for t in tracks["person"] if not on_billboard(t)]
    billboard = len(tracks["person"]) - len(people)
    cars = SAM.dedupe(tracks["bus"], tracks["car"])
    buses = tracks["bus"]
    taxis = taxi_ids(cars, images)
    roads = road_masks(images)
    kf_list = sorted(roads)

    def stab(f, p):
        o = offsets[min(f, len(offsets) - 1)]
        return (p[0] - o[0], p[1] - o[1])

    # walkers standing in the roadway: mask foot point inside SAM's road mask (nearest keyframe)
    in_road, road_seconds, foot_cache = set(), 0.0, {}
    for t in people:
        for f, d in zip(t.frames, t.dets):
            fx, fy = d.foot()
            foot_cache[(t.id, f)] = (fx, fy)
            kf = min(kf_list, key=lambda k: abs(k - f)) if kf_list else None
            if kf is None or abs(kf - f) > 3:
                continue
            xi, yi = int(min(W - 1, max(0, fx))), int(min(H - 1, max(0, fy - 1)))
            if roads[kf][yi, xi]:
                in_road.add(t.id)
                road_seconds += 1 / FPS

    # close calls: a walker in the roadway within one body height of a vehicle that is moving
    # relative to the street (pan-compensated)
    veh = cars + buses
    calls = []
    for t in people:
        if t.id not in in_road:
            continue
        for f, d in zip(t.frames, t.dets):
            if f == 0:
                continue
            fx, fy = foot_cache[(t.id, f)]
            hgt = d.box[3] - d.box[1]
            for v in veh:
                vd, vp = v.at(f), v.at(f - 1)
                if vd is None or vp is None:
                    continue
                cx = stab(f, ((vd.box[0] + vd.box[2]) / 2, 0))[0] - stab(f - 1, ((vp.box[0] + vp.box[2]) / 2, 0))[0]
                dx = max(vd.box[0] - fx, 0, fx - vd.box[2])
                dy = max(vd.box[1] - fy, 0, fy - vd.box[3])
                if abs(cx) > 1.5 and np.hypot(dx, dy) < hgt:
                    calls.append({"t": round(f / FPS, 2), "person": t.id, "vehicle": v.id})
                    break
    merged = []
    for c in sorted(calls, key=lambda c: c["t"]):
        if not merged or c["t"] - merged[-1]["t"] > 1.0:
            merged.append(c)

    # speeds, each object measured against its own size (the camera pans)
    walk = []
    for t in people:
        if t.n < 6:
            continue
        pts = np.array([stab(f, foot_cache[(t.id, f)]) for f in t.frames])
        scale = PERSON_H_M / max(float(np.median([b[3] - b[1] for b in t.boxes])), 1.0)
        dur = (t.frames[-1] - t.frames[0]) / FPS
        disp = float(np.linalg.norm(pts[-1] - pts[0])) * scale
        path = float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum()) * scale
        if dur > 0 and disp / max(path, 1e-6) > 0.5 and 0.5 < disp / dur < 2.5:
            walk.append(disp / dur)
    v_self, v_hom = [], []
    for v in cars:
        fr = [f for f in v.frames if f < 5 * FPS]
        if len(fr) < 4:
            continue
        bx = [v.at(f).box for f in fr]
        pts = np.array([stab(f, ((b[0] + b[2]) / 2, b[3])) for f, b in zip(fr, bx)])
        dur = (fr[-1] - fr[0]) / FPS
        wpx = float(np.median([b[2] - b[0] for b in bx]))
        v_self.append(float(np.linalg.norm(pts[-1] - pts[0])) / max(wpx, 1) * TAXI_LEN_M / dur)
        wrl = HG.apply(H_img2m, pts)
        v_hom.append(float(np.linalg.norm(wrl[-1] - wrl[0])) / dur)
    mv_self = [s for s in v_self if s > 0.5]
    mv_hom = [s for s in v_hom if s > 0.5]

    peak_img = max((len(v) for v in images.get("person", {}).values()), default=0)
    per_frame = {}
    for t in people:
        for f in t.frames:
            per_frame[f] = per_frame.get(f, 0) + 1
    confs = [d.conf for t in people for d in t.dets if d.conf is not None]
    m = {
        "people_tracked": len(people),
        "billboard_people_excluded": billboard,
        "people_peak_in_frame": peak_img,
        "people_peak_tracked_in_frame": max(per_frame.values()) if per_frame else 0,
        "people_in_roadway": len(in_road),
        "people_in_roadway_seconds": round(road_seconds, 1),
        "close_calls": len(merged),
        "close_call_times": [c["t"] for c in merged],
        "vehicles_tracked": len(cars) + len(buses),
        "vehicles_by_type": {"car": len(cars) - len(taxis), "taxi": len(taxis), "bus": len(buses)},
        "vehicle_speed_mps_median": round(float(np.median(mv_hom)), 2) if mv_hom else None,
        "vehicle_speed_mps_selfscale_median": round(float(np.median(mv_self)), 2) if mv_self else None,
        "vehicle_speed_n": len(mv_self),
        "walk_speed_mps_median": round(float(np.median(walk)), 2) if len(walk) >= 8 else None,
        "walk_speed_n": len(walk),
        "mean_confidence_person": round(float(np.mean(confs)), 3) if confs else None,
        "keyframes": len(kf_list),
    }
    extra = {"people": people, "cars": cars, "buses": buses, "taxis": taxis, "in_road": in_road,
             "foot": foot_cache, "roads": roads, "calls": merged}
    return m, extra


# ---------------------------------------------------------------- Spark
CONCEPT_SCHEMA = {
    "name": "concepts",
    "schema": {"type": "object", "additionalProperties": False, "required": ["concepts"],
               "properties": {"concepts": {"type": "array", "items": {
                   "type": "object", "additionalProperties": False, "required": ["phrase", "why"],
                   "properties": {"phrase": {"type": "string"}, "why": {"type": "string"}}}}}},
}
CONCEPT_PROMPT = """You are helping a segmentation model (SAM 3.1) measure pedestrian safety at this street
corner. SAM takes ONE short noun phrase per request (like "yellow taxi" or "metal barricade"); no
commands, lists, negation or spatial words. Name up to 5 physical things visible in this clip that
matter for walker safety and that "person", "car", "bus", "yellow taxi" and "road" do not already
cover (for example street furniture that narrows walking space, barriers, carts, cyclists).
For each, give the noun phrase and one sentence on why it matters."""


def spark_concepts(client, file_id) -> list[dict]:
    body = client.respond("spark", [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": CONCEPT_PROMPT}, {"type": "input_video", "file_id": file_id}]}],
        stage="spark:concepts", schema=CONCEPT_SCHEMA, reasoning="low", max_output_tokens=3000)
    return json.loads(output_text(body))["concepts"][:5]


def segment_concepts(client, concepts, kfs) -> list[dict]:
    frames = sorted(kfs)[:: max(1, len(kfs) // 3)][:3]
    out = []

    def one(job):
        c, f = job
        txt = client.sam(c["phrase"], image_url=kfs[f], stage=f"sam:image:spark-concept@{f}")
        dets = [d for d in SAM.parse_frames(txt).get(0, []) if (d.conf or 1) >= 0.5]
        return c, f, dets

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, [(c, f) for c in concepts for f in frames]))
    for c in concepts:
        hits = [(f, d) for cc, f, ds in res if cc is c for d in ds]
        out.append({**c, "found": len(hits), "frames": frames,
                    "boxes": [{"t": round(f / FPS, 2), "box": list(d.box), "conf": d.conf,
                               "mask": d.svg()} for f, d in hits][:24]})
    return out


def cross_check(spark: dict, extra) -> list[dict]:
    """Does SAM see the people / vehicles Spark describes at that moment?"""
    def present(tracks, t):
        f0, f1 = int((t - 0.6) * FPS), int((t + 0.6) * FPS)
        return sum(1 for tr in tracks if any(f0 <= f <= f1 for f in tr.frames))
    out = []
    for ev in spark.get("events", []):
        t = float(ev["t_s"])
        p = present(extra["people"], t)
        c = present(extra["cars"] + extra["buses"], t)
        needs_car = ev["kind"] in ("car_turning_through_walkers", "car_blocking_crosswalk", "driver_failed_to_yield",
                                   "traffic_queue")
        needs_person = ev["kind"] not in ("car_blocking_crosswalk", "traffic_queue")
        ok = (p > 0 or not needs_person) and (c > 0 or not needs_car)
        out.append({**ev, "sam_people": p, "sam_vehicles": c, "sam_agrees": bool(ok)})
    return out


def noncompliance_from_share(share_of_crossers: float, p_arrive_dont=0.55, p_go_otherwise=0.45 + 0.35 * 0.1):
    """Invert 'share of crossers who went against the signal' into the simulator's
    'share of walkers arriving on DON'T WALK who cross anyway'."""
    s = min(max(share_of_crossers, 0.0), 0.9)
    return s * p_go_otherwise / (p_arrive_dont * (1 - s))


def sim_inputs(m: dict, spark: dict, checks: list[dict]) -> dict:
    out = {}
    if m["walk_speed_mps_median"]:
        out["walk_speed_mps"] = {"v": m["walk_speed_mps_median"],
                                 "source": f"SAM 3.1: median speed of {m['walk_speed_n']} walkers moving in a "
                                           f"straight line, scaled by their own mask height (night, plaza edge)"}
    crossing = spark.get("walkers_crossing_roadway", 0)
    against = spark.get("walkers_crossing_against_signal", 0)
    agreed = [c for c in checks if c["kind"] == "crossing_against_signal" and c["sam_agrees"]]
    if crossing >= 3 and agreed:
        out["noncompliance"] = {"v": round(noncompliance_from_share(against / crossing), 3),
                                "source": f"Muse Spark: {against} of {crossing} walkers crossed against the signal "
                                          f"({len(agreed)} moment(s) confirmed by SAM tracks)"}
    return out


# ---------------------------------------------------------------- outputs
def tracks_json(extra, offsets, images) -> dict:
    items = [t.to_json(FPS) | {"label": "person", "in_road": t.id in extra["in_road"]} for t in extra["people"]]
    items += [t.to_json(FPS) | {"label": "taxi" if t.id in extra["taxis"] else "car"} for t in extra["cars"]]
    items += [t.to_json(FPS) | {"label": "bus"} for t in extra["buses"]]
    road = {str(round(f / FPS, 2)): [{"box": list(d.box), "mask": d.svg()} for d in dets]
            for f, dets in images.get("road", {}).items()}
    det_counts = {str(round(f / FPS, 2)): len(v) for f, v in images.get("person", {}).items()}
    return {"fps": FPS, "width": W, "height": H, "tracks": items, "road": road,
            "close_calls": extra["calls"], "image_person_counts": det_counts,
            "pan_offset_px": np.round(offsets, 1).tolist()}


def main(skip_api: bool = False):
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    MEDIA.mkdir(parents=True, exist_ok=True)
    info = V.probe(SRC)
    clean = V.sample(SRC, OUT / "clip_6fps.mp4", FPS, W)
    clock = V.sample(SRC, OUT / "clip_clock_6fps.mp4", FPS, W, clock=True)
    if not (MEDIA / "corner.mp4").exists():
        V.sample(SRC, MEDIA / "corner.mp4", 30, W)
    frames = V.gray_frames(clean, W, H)
    offsets = V.pan_offsets(frames)
    cal = json.loads(CAL.read_text())
    Hm = HG.solve(cal["image_px"], cal["world_m"])
    kfs = keyframe_jpegs(clean, len(frames))
    calls_path = OUT / "calls.json"

    if skip_api:
        images = load_images()
        tracks, manifest = track(None, None, images, len(frames))
        saved = json.loads((OUT / "spark.json").read_text())
        spark, concepts = saved["result"], saved.get("concepts", [])
        calls = json.loads(calls_path.read_text())
    else:
        for p in RAW.glob("*.txt"):
            p.unlink()
        client = MetaClient()
        models = client.resolve_models()
        images = detect_keyframes(client, kfs)
        fid = client.upload(clean, stage="upload:clip")
        tracks, manifest = track(client, fid, images, len(frames))
        cfid = client.upload(clock, stage="upload:clock-clip")
        with ThreadPoolExecutor(2) as ex:
            f_watch = ex.submit(SP.watch, client, cfid)
            f_conc = ex.submit(spark_concepts, client, cfid)
            spark, body = f_watch.result()
            proposed = f_conc.result()
        concepts = segment_concepts(client, proposed, kfs)
        (OUT / "spark.json").write_text(json.dumps({"model": models.get("spark"), "result": spark,
                                                     "concepts": concepts, "usage": body.get("usage")}, indent=1))
        calls = {"models": models, "calls": client.calls, "cost_usd": client.total_cost(),
                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        calls_path.write_text(json.dumps(calls, indent=1))

    m, extra = measure(tracks, images, offsets, Hm)
    checks = cross_check(spark, extra)
    sam_calls = [c for c in calls.get("calls", []) if c["stage"].startswith("sam")]
    out = {
        "clip": {"source": "data/footage/timessquare-2017.webm", "licence": "CC BY-SA 4.0, Nesnad (Wikimedia Commons)",
                 "duration_s": info["duration"], "analysed_fps": FPS, "frames": int(len(frames))},
        "site": "w45",
        "method": {
            "image_requests": sum(1 for c in sam_calls if ":image:" in c["stage"]),
            "track_requests": sum(1 for c in sam_calls if ":track:" in c["stage"]),
            "seeds": sum(r["seeds"] for r in manifest["requests"]),
            "conf_min": CONF_MIN, "batch": BATCH, "keyframe_every_s": KF_EVERY / FPS,
            "steps": ["image-mode detection of every instance on 1 s keyframes",
                      "new detections seeded as box prompts (<=16 per request), tracked forward",
                      "one-bit masks decoded: ground-contact points and the road surface",
                      "Spark proposes extra concepts; SAM segments them; SAM checks Spark's notes"]},
        "calibration": {**cal, "reprojection_error_m": round(HG.reprojection_error(Hm, cal["image_px"], cal["world_m"]), 4)},
        "pan_px_total": [round(float(x), 1) for x in offsets[-1]],
        "sam": m,
        "spark": {"events": checks, "totals": {k: v for k, v in spark.items() if k != "events"},
                  "agreement": round(sum(c["sam_agrees"] for c in checks) / len(checks), 2) if checks else None,
                  "concepts": concepts},
        "sim_inputs": sim_inputs(m, spark, checks),
        "models": calls.get("models", {}),
        "cost_usd": calls.get("cost_usd"),
    }
    (OUT / "measurements.json").write_text(json.dumps(out, indent=1))
    (OUT / "tracks.json").write_text(json.dumps(tracks_json(extra, offsets, images)))
    print(json.dumps({k: out[k] for k in ("method", "sam", "sim_inputs", "cost_usd")}, indent=1))
    print("spark agreement", out["spark"]["agreement"], "events", len(checks),
          "concepts", [(c["phrase"], c["found"]) for c in concepts])
    return out


if __name__ == "__main__":
    import sys
    main(skip_api="--offline" in sys.argv)
