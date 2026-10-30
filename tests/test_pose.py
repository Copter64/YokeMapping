from __future__ import annotations

import math
import random
from collections.abc import Sequence

import pytest

from yokemapping.tracking.pose import (
    Quat,
    pose_from_matrix,
    quaternion_from_rotation,
    rotation_from_quaternion,
    yaw_pitch_roll_degrees,
)

Mat3 = Sequence[Sequence[float]]


def _ry(deg: float) -> Mat3:
    a = math.radians(deg)
    return ((math.cos(a), 0, math.sin(a)), (0, 1, 0), (-math.sin(a), 0, math.cos(a)))


def _rx(deg: float) -> Mat3:
    a = math.radians(deg)
    return ((1, 0, 0), (0, math.cos(a), -math.sin(a)), (0, math.sin(a), math.cos(a)))


def _rz(deg: float) -> Mat3:
    a = math.radians(deg)
    return ((math.cos(a), -math.sin(a), 0), (math.sin(a), math.cos(a), 0), (0, 0, 1))


def _mul(a: Mat3, b: Mat3) -> Mat3:
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )


def _apply(m: Mat3, v: tuple[float, float, float]) -> tuple[float, ...]:
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


def _approx_quat(actual: Quat, expected: Quat) -> None:
    assert actual == pytest.approx(expected, abs=1e-9)


def test_identity_matrix_with_translation() -> None:
    m = ((1, 0, 0, 0.1), (0, 1, 0, 1.2), (0, 0, 1, -0.5))
    pose = pose_from_matrix(m, timestamp=12.5)
    assert pose.position == pytest.approx((0.1, 1.2, -0.5))
    _approx_quat(pose.orientation, (1, 0, 0, 0))
    assert pose.timestamp == 12.5


@pytest.mark.parametrize(
    ("matrix", "expected"),
    [
        (_ry(90), (math.cos(math.pi / 4), 0, math.sin(math.pi / 4), 0)),
        (_rx(180), (0, 1, 0, 0)),
        (_ry(180), (0, 0, 1, 0)),
        (_rz(180), (0, 0, 0, 1)),
    ],
)
def test_quaternion_from_rotation_all_branches(matrix: Mat3, expected: Quat) -> None:
    _approx_quat(quaternion_from_rotation(matrix), expected)


def test_round_trip_random_quaternions() -> None:
    rng = random.Random(1234)
    for _ in range(500):
        q = [rng.gauss(0, 1) for _ in range(4)]
        n = math.sqrt(sum(c * c for c in q))
        unit: Quat = (q[0] / n, q[1] / n, q[2] / n, q[3] / n)
        if unit[0] < 0:
            unit = (-unit[0], -unit[1], -unit[2], -unit[3])
        _approx_quat(quaternion_from_rotation(rotation_from_quaternion(unit)), unit)


@pytest.mark.parametrize(
    ("yaw", "pitch", "roll"),
    [(0, 0, 0), (30, 0, 0), (0, 20, 0), (0, 0, -45), (120, -35, 60), (-170, 80, 175)],
)
def test_yaw_pitch_roll_recovers_composition(yaw: float, pitch: float, roll: float) -> None:
    m = _mul(_mul(_ry(yaw), _rx(pitch)), _rz(roll))
    assert yaw_pitch_roll_degrees(quaternion_from_rotation(m)) == pytest.approx(
        (yaw, pitch, roll), abs=1e-6
    )


def test_documented_sign_conventions() -> None:
    forward = (0.0, 0.0, -1.0)
    right = (1.0, 0.0, 0.0)
    # +pitch tilts the forward axis up
    assert _apply(_rx(10), forward)[1] > 0
    # +yaw turns the forward axis to the left (-X)
    assert _apply(_ry(10), forward)[0] < 0
    # +roll lifts the right side
    assert _apply(_rz(10), right)[1] > 0
