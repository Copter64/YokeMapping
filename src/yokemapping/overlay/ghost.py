"""The ghost: where it sits in tracking space and what it looks like.

The real yoke's pose comes from the camera route (``faceplate_handeye.py`` writes
``handeye.json`` with the faceplate centre and rotation). The faceplate frame there is x right,
y down the photo, z into the plate; the overlay frame is x right, y up, z toward the pilot
(out of the plate), which is also the frame the offsets are expressed in.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw

from yokemapping.overlay.state import Offset

Mat = NDArray[np.float64]
PLATE_WIDTH_MM = 74.67  # owner's caliper measurement
PLATE_HEIGHT_MM = 119.6  # from the reference photo at 4.597 px/mm
GHOST_SIZE_MM = 400.0
PX_PER_MM = 2.0


class PoseFileError(ValueError):
    """The real-yoke pose file is missing or malformed."""


@dataclass(frozen=True, eq=False)
class YokePose:
    centre_m: NDArray[np.float64]  # position in tracking space, metres
    rotation: Mat  # columns: yoke x (right), y (up), z (toward the pilot) in tracking space


def load_real_pose(path: Path) -> YokePose:
    """Read the faceplate pose written by ``tools/faceplate_handeye.py`` (handeye.json)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        centre = np.asarray(data["faceplate_centre_tracking_m"], dtype=np.float64)
        rot = np.asarray(data["faceplate_rotation_tracking"], dtype=np.float64)
    except (OSError, ValueError, KeyError) as exc:
        raise PoseFileError(f"{path}: not a faceplate pose file ({exc})") from exc
    if centre.shape != (3,) or rot.shape != (3, 3) or not np.isfinite(rot).all():
        raise PoseFileError(f"{path}: expected a 3-vector centre and a 3x3 rotation")
    if not np.allclose(rot.T @ rot, np.eye(3), atol=1e-3) or np.linalg.det(rot) < 0:
        raise PoseFileError(f"{path}: the rotation is not a proper rotation matrix")
    flip = np.diag([1.0, -1.0, -1.0])  # photo frame (y down, z into plate) -> y up, z outward
    return YokePose(centre, rot @ flip)


def _rot_x(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float64)


def _rot_y(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float64)


def _rot_z(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float64)


def apply_offset(pose: YokePose, offset: Offset) -> YokePose:
    """The virtual yoke's pose: the real pose moved by ``offset`` in the yoke's own frame."""
    shift = np.array([offset.x_mm, offset.y_mm, offset.z_mm]) / 1000.0
    rot = _rot_y(offset.yaw_deg) @ _rot_x(offset.pitch_deg) @ _rot_z(offset.roll_deg)
    return YokePose(pose.centre_m + pose.rotation @ shift, pose.rotation @ rot)


def overlay_matrix(pose: YokePose) -> list[list[float]]:
    """3x4 row-major transform for an OpenVR overlay (overlay +z faces the pilot)."""
    return [
        [float(pose.rotation[r, c]) for c in range(3)] + [float(pose.centre_m[r])] for r in range(3)
    ]


def render_ghost() -> NDArray[np.uint8]:
    """RGBA texture: centre cross-hair, 50 mm ticks, circle and the faceplate outline.

    The texture is ``GHOST_SIZE_MM`` square at ``PX_PER_MM``; the overlay is made that wide so
    1 mm in the texture is 1 mm in the world.
    """
    size = round(GHOST_SIZE_MM * PX_PER_MM)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    mid = size / 2
    cyan, white = (0, 230, 255, 230), (255, 255, 255, 230)
    draw.line([(0, mid), (size, mid)], fill=cyan, width=2)
    draw.line([(mid, 0), (mid, size)], fill=cyan, width=2)
    for mm in range(-150, 151, 50):
        if mm:
            tick = mid + mm * PX_PER_MM
            draw.line([(tick, mid - 8), (tick, mid + 8)], fill=cyan, width=2)
            draw.line([(mid - 8, tick), (mid + 8, tick)], fill=cyan, width=2)
    r = 15 * PX_PER_MM
    draw.ellipse([mid - r, mid - r, mid + r, mid + r], outline=white, width=2)
    half_w, half_h = PLATE_WIDTH_MM * PX_PER_MM / 2, PLATE_HEIGHT_MM * PX_PER_MM / 2
    draw.rectangle([mid - half_w, mid - half_h, mid + half_w, mid + half_h], outline=white, width=2)
    return np.asarray(img, dtype=np.uint8)
