"""ffmpeg helpers: sampled clips, burned-in clock, chunks, frames, pan stabilisation."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def sh(*cmd) -> str:
    return subprocess.run([str(c) for c in cmd], check=True, capture_output=True, text=True).stdout


def probe(path: Path) -> dict:
    d = json.loads(sh("ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                      "-show_entries", "stream=width,height,nb_read_frames:format=duration", "-of", "json", path))
    s = d["streams"][0]
    return {"width": s["width"], "height": s["height"], "frames": int(s.get("nb_read_frames", 0)),
            "duration": float(d["format"]["duration"])}


def sample(src: Path, dst: Path, fps: int, width: int, clock: bool = False, start: float = 0.0,
           dur: float | None = None) -> Path:
    vf = f"fps={fps},scale={width}:-2"
    if clock:
        # Spark reads timestamps off this clock (+/-0.3 s instead of ~1 s early); the
        # clock shows source time, so chunks keep absolute timestamps
        vf += (f",drawtext=fontfile='{FONT}':text='t=%{{pts\\:flt\\:2}}':x=12:y=12:fontsize={width // 16}"
               f":fontcolor=white:box=1:boxcolor=black@0.85:boxborderw=8")
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", src]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-an", "-vf", vf, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", dst]
    sh(*cmd)
    return dst


def gray_frames(path: Path, width: int, height: int) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, height, width).astype(np.float32)


def phase_shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Translation (dx, dy) that maps a onto b, by phase correlation."""
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    A = np.fft.fft2((a - a.mean()) * win)
    B = np.fft.fft2((b - b.mean()) * win)
    R = A.conj() * B
    R /= np.abs(R) + 1e-9
    r = np.fft.ifft2(R).real
    y, x = np.unravel_index(np.argmax(r), r.shape)
    h, w = r.shape
    dy = y if y <= h // 2 else y - h
    dx = x if x <= w // 2 else x - w
    return float(dx), float(dy)


def pan_offsets(frames: np.ndarray, band=(0.05, 0.45)) -> np.ndarray:
    """Cumulative camera offset per frame from the static building band (top of frame).
    offset[i] maps frame-i pixel coords into frame-0 coords: p0 = p_i - offset[i]."""
    h = frames.shape[1]
    y0, y1 = int(h * band[0]), int(h * band[1])
    off = np.zeros((len(frames), 2))
    for i in range(1, len(frames)):
        dx, dy = phase_shift(frames[i - 1, y0:y1], frames[i, y0:y1])
        off[i] = off[i - 1] + (dx, dy)
    return off
