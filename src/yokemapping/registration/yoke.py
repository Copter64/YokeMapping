"""The yoke's coordinate frame from touched landmarks, and real-to-virtual alignment.

Landmarks, touched with the controller tip in this order:

- ``shaft_1`` .. ``shaft_3``: points along the yoke's roll axis (the shaft/column), from
  the base toward the pilot-side hub. At least two are required; three give a residual.
- ``left_grip`` / ``right_grip``: the centre of each grip, as seen by the pilot.

Yoke frame: ``+x`` to the pilot's right, ``+y`` up, ``+z`` back toward the pilot (the pull
direction), i.e. right-handed with ``x`` x ``y`` = ``z``; this matches OpenVR's -Z-forward.
The origin is where the shaft axis passes the midpoint of the grips.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from yokemapping.registration.geometry import (
    GeometryError,
    Mat,
    RigidTransform,
    Vec3,
    as_points,
    closest_point_on_line,
    fit_line,
    kabsch,
    to_vec3,
)
from yokemapping.tracking.pose import quaternion_from_rotation, yaw_pitch_roll_degrees

SHAFT_NAMES = ("shaft_1", "shaft_2", "shaft_3")
GRIP_NAMES = ("left_grip", "right_grip")
LANDMARK_NAMES = (*SHAFT_NAMES, *GRIP_NAMES)
MFY_NAMES = ("top_left", "top_right", "bottom_center", "left_grip", "right_grip")
LAYOUTS: dict[str, tuple[str, ...]] = {"mfy": MFY_NAMES, "column": LANDMARK_NAMES}
ALL_NAMES = tuple(dict.fromkeys((*LANDMARK_NAMES, *MFY_NAMES)))
MFY_TOP_GAP_M = 0.155  # owner's measurement of the closest points across the top of the MFY


@dataclass(frozen=True, eq=False)
class MfyFrame:
    """Frame of a butterfly (MFY-style) yoke: origin at the middle of the top gap."""

    origin: Vec3
    rotation: Mat  # columns are x (right), y (up), z (back toward the pilot)
    top_gap_m: float
    height_m: float  # top-gap midpoint to the bottom-centre landmark
    grip_width_m: float
    skew_deg: float  # how far the bottom-centre point is from straight below the gap midpoint

    def warnings(self) -> list[str]:
        found: list[str] = []
        if abs(self.top_gap_m - MFY_TOP_GAP_M) > 0.015:
            found.append(
                f"top gap {self.top_gap_m * 1000:.0f} mm differs from the expected "
                f"{MFY_TOP_GAP_M * 1000:.0f} mm by more than 15 mm"
            )
        if self.height_m < 0.08:
            found.append(f"bottom-centre is only {self.height_m * 1000:.0f} mm below the top gap")
        if self.skew_deg > 10:
            found.append(f"bottom-centre is {self.skew_deg:.0f} deg off from centred under the gap")
        return found


def mfy_frame_from_landmarks(landmarks: Mapping[str, Vec3]) -> MfyFrame:
    missing = [n for n in MFY_NAMES if n not in landmarks]
    if missing:
        raise GeometryError(f"missing landmark(s): {', '.join(missing)}")
    left = np.asarray(landmarks["top_left"], dtype=np.float64)
    right = np.asarray(landmarks["top_right"], dtype=np.float64)
    bottom = np.asarray(landmarks["bottom_center"], dtype=np.float64)
    gap_vec = right - left
    gap = float(np.linalg.norm(gap_vec))
    if gap < 1e-6:
        raise GeometryError("top_left and top_right coincide")
    x = gap_vec / gap
    mid = (left + right) / 2.0
    up = mid - bottom
    height = float(np.linalg.norm(up))
    up_perp = up - float(up @ x) * x
    if np.linalg.norm(up_perp) < 1e-6:
        raise GeometryError("bottom_center is in line with the top gap; check the landmarks")
    y = up_perp / np.linalg.norm(up_perp)
    z = np.cross(x, y)
    skew = math.degrees(math.acos(float(np.clip(up @ x / height, -1.0, 1.0)))) - 90.0
    grip_vec = np.asarray(landmarks["right_grip"]) - np.asarray(landmarks["left_grip"])
    return MfyFrame(
        origin=to_vec3(mid),
        rotation=np.column_stack([x, y, z]),
        top_gap_m=gap,
        height_m=height,
        grip_width_m=float(np.linalg.norm(grip_vec)),
        skew_deg=abs(skew),
    )


@dataclass(frozen=True, eq=False)
class YokeFrame:
    origin: Vec3
    rotation: Mat  # columns are the frame's x (right), y (up), z (back) axes in world space
    shaft_rms_m: float
    shaft_length_m: float
    grip_width_m: float
    grip_offset_m: float  # distance of the grip midpoint from the shaft axis
    skew_deg: float  # how far the grip line is from perpendicular to the shaft

    def matrix(self) -> Mat:
        m = np.eye(4)
        m[:3, :3] = self.rotation
        m[:3, 3] = self.origin
        return m

    def warnings(self) -> list[str]:
        """Plain-language reasons to distrust this capture (empty when it looks sane)."""
        found: list[str] = []
        if self.shaft_rms_m > 0.005:
            found.append(f"shaft points are {self.shaft_rms_m * 1000:.1f} mm off a straight line")
        if self.shaft_length_m < 0.05:
            found.append(f"shaft points span only {self.shaft_length_m * 1000:.0f} mm")
        if self.skew_deg > 10:
            found.append(f"grip line is {self.skew_deg:.0f} deg from perpendicular to the shaft")
        if self.grip_offset_m > 0.03:
            found.append(f"grip midpoint is {self.grip_offset_m * 1000:.0f} mm off the shaft axis")
        if not 0.15 < self.grip_width_m < 0.60:
            found.append(f"grip spacing {self.grip_width_m * 1000:.0f} mm looks wrong for a yoke")
        return found


def build_yoke_frame(
    shaft_points: Sequence[Vec3] | Mat, left_grip: Vec3, right_grip: Vec3
) -> YokeFrame:
    """Build the frame; ``shaft_points`` are ordered from the base toward the pilot."""
    shaft = as_points(shaft_points, minimum=2)
    back_hint = to_vec3(shaft[-1] - shaft[0])
    line = fit_line(shaft, direction_hint=back_hint)
    z = np.asarray(line.direction)

    left, right = np.asarray(left_grip, dtype=np.float64), np.asarray(right_grip, dtype=np.float64)
    lateral = right - left
    width = float(np.linalg.norm(lateral))
    if width < 1e-6:
        raise GeometryError("left and right grip points coincide")
    skew = abs(90.0 - math.degrees(math.acos(float(np.clip(lateral @ z / width, -1.0, 1.0)))))
    x = lateral - float(lateral @ z) * z
    if np.linalg.norm(x) < 1e-6 * width:
        raise GeometryError("the grip line is parallel to the shaft; check the landmarks")
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)

    mid = (left + right) / 2.0
    origin = np.asarray(closest_point_on_line(line, to_vec3(mid)))
    along = (shaft - np.asarray(line.point)) @ z
    return YokeFrame(
        origin=to_vec3(origin),
        rotation=np.column_stack([x, y, z]),
        shaft_rms_m=line.rms_m,
        shaft_length_m=float(along.max() - along.min()),
        grip_width_m=width,
        grip_offset_m=float(np.linalg.norm(mid - origin)),
        skew_deg=skew,
    )


def frame_from_landmarks(landmarks: Mapping[str, Vec3]) -> YokeFrame:
    """Build the frame from a landmark dict; needs both grips and at least two shaft points."""
    missing = [n for n in GRIP_NAMES if n not in landmarks]
    if missing:
        raise GeometryError(f"missing landmark(s): {', '.join(missing)}")
    shaft = [landmarks[n] for n in SHAFT_NAMES if n in landmarks]
    if len(shaft) < 2:
        raise GeometryError("need at least two shaft landmarks")
    return build_yoke_frame(shaft, landmarks["left_grip"], landmarks["right_grip"])


# --- alignment --------------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class Alignment:
    """Maps the real yoke's landmarks onto the virtual yoke's (``virtual = T @ real``)."""

    transform: RigidTransform
    names: tuple[str, ...]
    yaw_only: bool

    def residuals_mm(self) -> dict[str, float]:
        return {n: r * 1000.0 for n, r in zip(self.names, self.transform.residuals_m, strict=True)}


def align_landmarks(
    real: Mapping[str, Vec3], virtual: Mapping[str, Vec3], *, yaw_only: bool = False
) -> Alignment:
    names = tuple(n for n in ALL_NAMES if n in real and n in virtual)
    minimum = 2 if yaw_only else 3
    if len(names) < minimum:
        raise GeometryError(
            f"need at least {minimum} landmarks present in both captures; "
            f"common landmarks: {', '.join(names) or 'none'}"
        )
    tf = kabsch([real[n] for n in names], [virtual[n] for n in names], yaw_only=yaw_only)
    return Alignment(transform=tf, names=names, yaw_only=yaw_only)


@dataclass(frozen=True)
class TransformSummary:
    translation_mm: Vec3
    yaw_deg: float
    pitch_deg: float
    roll_deg: float


def summarize_transform(tf: RigidTransform) -> TransformSummary:
    q = quaternion_from_rotation(tf.rotation.tolist())
    yaw, pitch, roll = yaw_pitch_roll_degrees(q)
    t = tf.translation * 1000.0
    return TransformSummary((float(t[0]), float(t[1]), float(t[2])), yaw, pitch, roll)
