"""Image plane -> street plane (metres) with a 4-point homography (DLT)."""
from __future__ import annotations

import numpy as np


def solve(img_pts, world_pts) -> np.ndarray:
    """3x3 H with world ~ H @ [x, y, 1] from >= 4 correspondences."""
    img_pts = np.asarray(img_pts, float)
    world_pts = np.asarray(world_pts, float)
    if len(img_pts) < 4 or img_pts.shape != world_pts.shape:
        raise ValueError("need >= 4 matching points")
    rows = []
    for (x, y), (u, v) in zip(img_pts, world_pts):
        rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, _, vt = np.linalg.svd(np.array(rows))
    H = vt[-1].reshape(3, 3)
    return H / H[2, 2]


def apply(H: np.ndarray, pts) -> np.ndarray:
    pts = np.atleast_2d(np.asarray(pts, float))
    hom = np.c_[pts, np.ones(len(pts))] @ H.T
    return hom[:, :2] / hom[:, 2:3]


def reprojection_error(H, img_pts, world_pts) -> float:
    return float(np.abs(apply(H, img_pts) - np.asarray(world_pts, float)).max())
