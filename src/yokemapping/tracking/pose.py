"""Pose types and rotation math, independent of where poses come from.

Coordinate conventions follow OpenVR's tracking space: right-handed, meters,
+X right, +Y up, -Z forward. Quaternions are (w, x, y, z), unit length, w >= 0.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]
Matrix3x4 = Sequence[Sequence[float]]


class TrackingStatus(Enum):
    OK = "ok"
    TRACKING_LOST = "tracking_lost"  # device connected but its pose is not valid
    DEVICE_NOT_FOUND = "device_not_found"  # no connected device matches the selector
    RUNTIME_UNAVAILABLE = "runtime_unavailable"  # e.g. SteamVR not running


@dataclass(frozen=True)
class Pose:
    position: Vec3
    orientation: Quat
    timestamp: float  # time.monotonic() seconds when sampled


@dataclass(frozen=True)
class PoseSample:
    status: TrackingStatus
    pose: Pose | None = None


def quaternion_from_rotation(m: Matrix3x4) -> Quat:
    """Convert the rotation part (first 3 columns) of a 3x3/3x4 matrix to a unit quaternion."""
    r00, r01, r02 = m[0][0], m[0][1], m[0][2]
    r10, r11, r12 = m[1][0], m[1][1], m[1][2]
    r20, r21, r22 = m[2][0], m[2][1], m[2][2]
    trace = r00 + r11 + r22
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        w, x, y, z = 0.25 * s, (r21 - r12) / s, (r02 - r20) / s, (r10 - r01) / s
    elif r00 > r11 and r00 > r22:
        s = math.sqrt(1.0 + r00 - r11 - r22) * 2
        w, x, y, z = (r21 - r12) / s, 0.25 * s, (r01 + r10) / s, (r02 + r20) / s
    elif r11 > r22:
        s = math.sqrt(1.0 + r11 - r00 - r22) * 2
        w, x, y, z = (r02 - r20) / s, (r01 + r10) / s, 0.25 * s, (r12 + r21) / s
    else:
        s = math.sqrt(1.0 + r22 - r00 - r11) * 2
        w, x, y, z = (r10 - r01) / s, (r02 + r20) / s, (r12 + r21) / s, 0.25 * s
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    sign = -1.0 if w < 0 else 1.0
    return (sign * w / norm, sign * x / norm, sign * y / norm, sign * z / norm)


def rotation_from_quaternion(q: Quat) -> tuple[Vec3, Vec3, Vec3]:
    w, x, y, z = q
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )


def pose_from_matrix(m: Matrix3x4, timestamp: float) -> Pose:
    """Build a Pose from a 3x4 [R|t] device-to-world matrix (e.g. OpenVR HmdMatrix34_t.m)."""
    return Pose(
        position=(float(m[0][3]), float(m[1][3]), float(m[2][3])),
        orientation=quaternion_from_rotation(m),
        timestamp=timestamp,
    )


def yaw_pitch_roll_degrees(q: Quat) -> Vec3:
    """Decompose as R = Ry(yaw) @ Rx(pitch) @ Rz(roll).

    Signs (looking along -Z): +yaw turns left, +pitch tilts the nose up, +roll lifts the
    right side (rolls left).
    """
    r = rotation_from_quaternion(q)
    pitch = math.asin(max(-1.0, min(1.0, -r[1][2])))
    yaw = math.atan2(r[0][2], r[2][2])
    roll = math.atan2(r[1][0], r[1][1])
    return (math.degrees(yaw), math.degrees(pitch), math.degrees(roll))
