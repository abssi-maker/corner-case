"""SAM 3.1 output -> detections and tracks, with masks.

Grammar (dev.meta.ai/docs/sam/reading-segmentation): each line is `<Nf>` then
comma-separated records `id<|box;x1;y1;x2;y2;w;h[;c]|><|mask;x=0;y=0[;c];data=H,W,<enc>payload|>`.
Boxes are inclusive source pixels; the mask raster is H x W and covers the box.
Ids are stable per object while it stays in view, so an id within one request is a track.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
from meta_sam_parser import SegmentationMask, decode_mask_to_raster, decode_mask_to_svg_path

FRAME_RE = re.compile(r"<(\d+)f>")
REC_RE = re.compile(
    r"(\d+)<\|box;x1=(-?\d+);y1=(-?\d+);x2=(-?\d+);y2=(-?\d+);w=(\d+);h=(\d+)(?:;c=([\d.]+))?\|>"
    r"(?:<\|mask;x=0;y=0(?:;c=[\d.]+)?;data=(\d+),(\d+),([^|]*)\|>)?")


_CMD_RE = re.compile(r"([MLZ])\s*([-\d.]+)?[ ,]?([-\d.]+)?")


def _rdp(pts: np.ndarray, eps: float) -> np.ndarray:
    """Ramer-Douglas-Peucker polyline simplification."""
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    ab = b - a
    n = np.hypot(*ab)
    rel = pts - a
    d = np.abs(ab[0] * rel[:, 1] - ab[1] * rel[:, 0]) / n if n > 0 else np.hypot(rel[:, 0], rel[:, 1])
    i = int(np.argmax(d))
    if d[i] > eps:
        return np.vstack([_rdp(pts[: i + 1], eps)[:-1], _rdp(pts[i:], eps)])
    return np.vstack([a, b])


def simplify_path(path: str, eps: float = 0.75) -> str:
    """Shrink a pixel-staircase SVG mask outline (M/L/Z only) for the UI."""
    subs, cur = [], []
    for cmd, x, y in _CMD_RE.findall(path):
        if cmd == "M":
            if cur:
                subs.append(cur)
            cur = [(float(x), float(y))]
        elif cmd == "L":
            cur.append((float(x), float(y)))
        elif cmd == "Z" and cur:
            subs.append(cur)
            cur = []
    if cur:
        subs.append(cur)
    out = []
    for sp in subs:
        if len(sp) < 3:
            continue
        q = _rdp(np.array(sp), eps)
        out.append("M" + "L".join(f"{x:g} {y:g}" for x, y in np.round(q, 1)) + "Z")
    return "".join(out)


@dataclass
class Det:
    id: int
    box: tuple[int, int, int, int]   # x1, y1, x2, y2 (exclusive right/bottom)
    conf: float | None = None
    mask: tuple[int, int, str] | None = None  # (H, W, payload)

    def raster(self) -> np.ndarray | None:
        if not self.mask:
            return None
        h, w, payload = self.mask
        enc = "lossless" if payload.startswith("~") else "one_bit"
        raw = decode_mask_to_raster(SegmentationMask(encoding=enc, payload=payload, width=w, height=h))
        return np.frombuffer(raw, np.uint8).reshape(h, w)

    def svg(self) -> str | None:
        if not self.mask:
            return None
        h, w, payload = self.mask
        enc = "lossless" if payload.startswith("~") else "one_bit"
        return simplify_path(decode_mask_to_svg_path(SegmentationMask(encoding=enc, payload=payload, width=w, height=h)))

    def foot(self) -> tuple[float, float]:
        """Ground contact point: centre of the mask's lowest rows (box bottom-centre fallback)."""
        x1, y1, x2, y2 = self.box
        r = self.raster()
        if r is None or not r.any():
            return ((x1 + x2) / 2, y2)
        rows = np.nonzero(r.any(axis=1))[0]
        low = rows[-max(1, len(rows) // 12):]
        cols = np.nonzero(r[low].any(axis=0))[0]
        return (x1 + float(cols.mean()) + 0.5, y1 + float(low[-1]) + 1)


@dataclass
class Track:
    id: int
    label: str
    frames: list[int] = field(default_factory=list)
    dets: list[Det] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.frames)

    @property
    def boxes(self):
        return [d.box for d in self.dets]

    def at(self, f: int) -> Det | None:
        try:
            return self.dets[self.frames.index(f)]
        except ValueError:
            return None

    def to_json(self, fps: float, masks: bool = True) -> dict:
        out = {"id": self.id, "label": self.label, "t": [round(f / fps, 3) for f in self.frames],
               "box": [list(d.box) for d in self.dets],
               "conf": [None if d.conf is None else round(d.conf, 3) for d in self.dets]}
        if masks:
            out["mask"] = [d.svg() for d in self.dets]
        return out


def parse_frames(text: str) -> dict[int, list[Det]]:
    frames: dict[int, list[Det]] = {}
    parts = FRAME_RE.split(text)
    chunks = [(0, parts[0])] if parts[0].strip() else []
    chunks += [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    for fi, chunk in chunks:
        for m in REC_RE.finditer(chunk):
            x1, y1, x2, y2 = (int(m.group(i)) for i in range(2, 6))
            frames.setdefault(fi, []).append(Det(
                id=int(m.group(1)), box=(x1, y1, x2 + 1, y2 + 1),
                conf=float(m.group(8)) if m.group(8) else None,
                mask=(int(m.group(9)), int(m.group(10)), m.group(11)) if m.group(9) else None))
    return frames


def tracks_from_text(text: str, label: str, min_frames: int = 3, id_offset: int = 0) -> list[Track]:
    by_id: dict[int, Track] = {}
    for fi, dets in sorted(parse_frames(text).items()):
        for d in dets:
            t = by_id.setdefault(d.id, Track(d.id + id_offset, label))
            if fi in t.frames:
                continue
            t.frames.append(fi)
            t.dets.append(d)
    out = []
    for t in by_id.values():
        order = np.argsort(t.frames)
        t.frames = [t.frames[i] for i in order]
        t.dets = [t.dets[i] for i in order]
        if t.n >= min_frames:
            out.append(t)
    return out


def iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter == 0:
        return 0.0
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])
    return inter / (area(a) + area(b) - inter)


def dedupe(primary: list[Track], secondary: list[Track], thr: float = 0.5) -> list[Track]:
    """Secondary tracks that do not mostly overlap a primary track in shared frames."""
    keep = []
    for s in secondary:
        sb = dict(zip(s.frames, s.boxes))
        dup = False
        for p in primary:
            shared = [f for f in p.frames if f in sb]
            if len(shared) >= max(2, s.n // 3):
                pb = dict(zip(p.frames, p.boxes))
                if sum(iou(pb[f], sb[f]) > thr for f in shared) >= len(shared) / 2:
                    dup = True
                    break
        if not dup:
            keep.append(s)
    return keep
