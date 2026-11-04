import json
from pathlib import Path

import numpy as np
import pytest

from cornercase import evidence as EV
from cornercase.agents.tools import parse_changes
from cornercase.data import crashes as CR
from cornercase.data.geo import dist_m
from cornercase.vision import homography as HG
from cornercase.vision import sam as SAM
from cornercase.vision.pipeline import noncompliance_from_share, road_masks
from cornercase.vision.video import phase_shift

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- crash snapping
def test_snap_by_location_not_name():
    nodes = [{"id": "a", "lat": 40.7580, "lon": -73.9855}, {"id": "b", "lat": 40.7590, "lon": -73.9848}]
    crashes = [
        {"id": "1", "lat": 40.75802, "lon": -73.98552, "injured": 1, "killed": 0, "date": "2025-02-01"},  # ~3 m from a
        {"id": "2", "lat": 40.75899, "lon": -73.98479, "injured": 1, "killed": 1, "date": "2024-05-01"},  # ~1 m from b
        {"id": "3", "lat": 40.7620, "lon": -73.9800, "injured": 1, "killed": 0, "date": "2025-03-01"},    # far away
    ]
    s = CR.snap(crashes, nodes)
    assert {c["id"]: c["node"] for c in s} == {"1": "a", "2": "b"}
    agg = CR.per_node(s, ["a", "b"], CR.HELDOUT)
    assert agg["a"]["crashes"] == 1 and agg["b"]["crashes"] == 0


def test_snap_radius_boundary():
    nodes = [{"id": "a", "lat": 40.7580, "lon": -73.9855}]
    lat_45m = 40.7580 + 45 / 111_132
    s = CR.snap([{"id": "x", "lat": lat_45m, "lon": -73.9855, "injured": 1, "killed": 0, "date": "2025-01-02"}], nodes)
    assert s == []
    assert dist_m(lat_45m, -73.9855, 40.7580, -73.9855) == pytest.approx(45, abs=0.5)


def test_real_crash_file_counts():
    rows = json.loads(CR.CRASHES.read_text())
    assert len(rows) == 6266
    assert sum(int(r.get("number_of_pedestrians_killed") or 0) for r in rows) == 77
    assert sum(int(r.get("number_of_pedestrians_injured") or 0) for r in rows) == 6441


def test_corridor_built():
    cor = json.loads((ROOT / "data" / "corridor.json").read_text())["intersections"]
    assert len(cor) == 30
    ids = [c["id"] for c in cor]
    assert ids[0] == "w30" and ids[-1] == "w59"
    w45 = next(c for c in cor if c["id"] == "w45")
    assert w45["cross_dirs"] == ["WB"] and w45["broadway"] and w45["lpi_installed"]
    assert next(c for c in cor if c["id"] == "w42")["cross_dirs"] == ["EB", "WB"]


# ---------------------------------------------------------------- homography / geometry
def test_homography_recovers_known_transform():
    H_true = np.array([[0.05, 0.01, -2.0], [0.002, 0.08, -50.0], [0.0001, 0.0004, 1.0]])
    img = np.array([[20, 785], [520, 760], [20, 900], [520, 870], [300, 820]], float)
    world = HG.apply(H_true, img)
    H = HG.solve(img[:4], world[:4])
    assert HG.reprojection_error(H, img, world) < 1e-6


def test_calibration_file_is_consistent():
    cal = json.loads((ROOT / "data" / "vision" / "calibration.json").read_text())
    H = HG.solve(cal["image_px"], cal["world_m"])
    assert HG.reprojection_error(H, cal["image_px"], cal["world_m"]) < 1e-6
    mid = HG.apply(H, [[270, 830]])[0]
    assert 0 < mid[0] < 12.2 and 0 < mid[1] < 3.0


def test_homography_needs_four_points():
    with pytest.raises(ValueError):
        HG.solve([[0, 0], [1, 0], [0, 1]], [[0, 0], [1, 0], [0, 1]])


def test_phase_correlation_finds_shift():
    rng = np.random.default_rng(0)
    a = rng.normal(size=(64, 96))
    b = np.roll(a, shift=(3, -7), axis=(0, 1))
    assert phase_shift(a, b) == (-7.0, 3.0)


# ---------------------------------------------------------------- SAM parsing
SAMPLE = ("<0f>0<|box;x1=10;y1=20;x2=30;y2=60;w=540;h=960|><|mask;x=0;y=0;data=ab|>,"
          "1<|box;x1=100;y1=200;x2=150;y2=260;w=540;h=960|><|mask;x=0;y=0;data=cd|>\n"
          "<1f>0<|box;x1=12;y1=21;x2=32;y2=61;w=540;h=960|>\n"
          "<2f>0<|box;x1=14;y1=22;x2=34;y2=62;w=540;h=960|>\n")


def test_sam_parse_tracks():
    fr = SAM.parse_frames(SAMPLE)
    assert sorted(fr) == [0, 1, 2] and len(fr[0]) == 2
    tr = SAM.tracks_from_text(SAMPLE, "person", min_frames=3)
    assert len(tr) == 1 and tr[0].frames == [0, 1, 2]
    assert tr[0].boxes[2] == (14, 22, 35, 63)  # inclusive SAM box -> exclusive right/bottom


def test_sam_parse_confidence_and_mask_fields():
    line = "<3f>7<|box;x1=1;y1=2;x2=4;y2=8;w=540;h=960;c=0.875|><|mask;x=0;y=0;c=0.875;data=7,4,!abc|>"
    d = SAM.parse_frames(line)[3][0]
    assert d.id == 7 and d.conf == 0.875 and d.mask == (7, 4, "!abc") and d.box == (1, 2, 5, 9)


def test_dedupe_overlapping_tracks():
    mk = lambda i, box: SAM.Track(i, "car", [0, 1, 2], [SAM.Det(i, box)] * 3)
    a, b, c = mk(1, (0, 0, 10, 10)), mk(2, (1, 1, 10, 10)), mk(3, (50, 50, 60, 60))
    assert [t.id for t in SAM.dedupe([a], [b, c])] == [3]


def test_noncompliance_inversion_roundtrip():
    p = noncompliance_from_share(0.2)
    share = p * 0.55 / (p * 0.55 + 0.45 + 0.035)
    assert share == pytest.approx(0.2, abs=1e-9)


# ---------------------------------------------------------------- trust metrics
def test_spearman_and_ties():
    assert EV.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert EV.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert list(EV.rankdata([5, 1, 5, 2])) == [3.5, 1.0, 3.5, 2.0]


def test_topk_overlap():
    assert EV.topk_overlap([9, 8, 7, 1, 0], [9, 8, 0, 7, 1], 2) == 2
    assert EV.topk_overlap([0, 1, 2, 8, 9], [9, 8, 7, 1, 0], 2) == 0


# ---------------------------------------------------------------- agent tool parsing
def test_parse_changes_tolerates_null_strings():
    ch = parse_changes({"lpi_s": "null", "turn_ban": "None", "daylighting": True, "cycle_s": None})
    assert ch.lpi_s is None and ch.turn_ban == "none" and ch.daylighting is True


def test_parse_changes_rejects_out_of_range():
    with pytest.raises(Exception):
        parse_changes({"lpi_s": 30})


def test_real_masks_decode_inside_their_box():
    raw = sorted((ROOT / "data" / "vision" / "sam_raw").glob("img_person_*.txt"))
    if not raw:
        pytest.skip("no saved SAM responses")
    dets = SAM.parse_frames(raw[0].read_text())[0]
    d = dets[0]
    r = d.raster()
    assert r.shape == (d.box[3] - d.box[1], d.box[2] - d.box[0]) and r.any()
    fx, fy = d.foot()
    assert d.box[0] <= fx <= d.box[2] and d.box[1] < fy <= d.box[3]
    assert d.svg().startswith("M")
