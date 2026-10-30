from __future__ import annotations

import math

import numpy as np
import pytest
from numpy.typing import NDArray

from yokemapping.registration.geometry import (
    GeometryError,
    closest_point_on_line,
    distance_to_line,
    fit_line,
    kabsch,
    pivot_calibration,
    rotation_spread_deg,
)

Mat = NDArray[np.float64]


def rot_axis_angle(axis: Mat | list[float], angle_rad: float) -> Mat:
    a = np.asarray(axis, dtype=np.float64)
    a = a / np.linalg.norm(a)
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.asarray(np.eye(3) + math.sin(angle_rad) * k + (1 - math.cos(angle_rad)) * k @ k)


def rot_y(angle_rad: float) -> Mat:
    return rot_axis_angle([0, 1, 0], angle_rad)


# --- fit_line ---------------------------------------------------------------------------------


def test_fit_line_recovers_noisy_line() -> None:
    rng = np.random.default_rng(1)
    direction = np.array([0.2, 0.3, -0.9])
    direction /= np.linalg.norm(direction)
    origin = np.array([0.1, 0.8, -0.5])
    t = np.linspace(-0.15, 0.15, 25)
    pts = origin + np.outer(t, direction) + rng.normal(scale=0.0005, size=(25, 3))
    line = fit_line(
        pts, direction_hint=(float(direction[0]), float(direction[1]), float(direction[2]))
    )
    assert np.asarray(line.direction) == pytest.approx(direction, abs=5e-3)
    assert distance_to_line(line, (float(origin[0]), float(origin[1]), float(origin[2]))) < 1e-3
    assert 0.0002 < line.rms_m < 0.001
    assert line.n == 25


def test_fit_line_hint_sets_sign() -> None:
    pts = [(0, 0, 0), (0, 0, 1), (0, 0, 2)]
    assert fit_line(pts, direction_hint=(0, 0, 1)).direction == pytest.approx((0, 0, 1))
    assert fit_line(pts, direction_hint=(0, 0, -1)).direction == pytest.approx((0, 0, -1))


def test_fit_line_two_points_is_exact() -> None:
    line = fit_line([(0, 0, 0), (3, 4, 0)])
    assert line.rms_m == pytest.approx(0, abs=1e-12)
    assert abs(line.direction[0]) == pytest.approx(0.6)


def test_fit_line_rejects_coincident_and_short_input() -> None:
    with pytest.raises(GeometryError, match="coincide"):
        fit_line([(1, 1, 1), (1, 1, 1)])
    with pytest.raises(GeometryError, match="at least 2"):
        fit_line([(1, 1, 1)])
    with pytest.raises(GeometryError, match="NaN"):
        fit_line([(0, 0, 0), (float("nan"), 0, 1)])
    with pytest.raises(GeometryError, match="shape"):
        fit_line([(0, 0), (1, 1)])


def test_closest_point_and_distance() -> None:
    line = fit_line([(0, 0, 0), (1, 0, 0)])
    assert closest_point_on_line(line, (0.4, 3.0, 4.0)) == pytest.approx((0.4, 0, 0), abs=1e-9)
    assert distance_to_line(line, (0.4, 3.0, 4.0)) == pytest.approx(5.0)


# --- kabsch -----------------------------------------------------------------------------------


def random_rigid(rng: np.random.Generator) -> tuple[Mat, Mat]:
    rot = rot_axis_angle(rng.normal(size=3), rng.uniform(-math.pi, math.pi))
    return rot, rng.uniform(-1, 1, size=3)


def test_kabsch_recovers_transform_with_noise() -> None:
    rng = np.random.default_rng(2)
    rot, trans = random_rigid(rng)
    src = rng.uniform(-0.3, 0.3, size=(6, 3))
    dst = src @ rot.T + trans + rng.normal(scale=0.0005, size=(6, 3))
    tf = kabsch(src, dst)
    # rotation error ~ noise / point spread = 0.5 mm / ~0.2 m = 2.5e-3 rad per element
    assert tf.rotation == pytest.approx(rot, abs=8e-3)
    assert tf.translation == pytest.approx(trans, abs=4e-3)
    assert 0.0002 < tf.rms_m < 0.001
    assert len(tf.residuals_m) == 6
    assert float(np.linalg.det(tf.rotation)) == pytest.approx(1.0)
    assert tf.apply(src) == pytest.approx(dst, abs=3e-3)


def test_kabsch_exact_and_matrix() -> None:
    rng = np.random.default_rng(3)
    rot, trans = random_rigid(rng)
    src = rng.uniform(-0.2, 0.2, size=(5, 3))
    tf = kabsch(src, src @ rot.T + trans)
    assert tf.rms_m == pytest.approx(0, abs=1e-12)
    m = tf.matrix()
    assert m[:3, :3] == pytest.approx(rot, abs=1e-9)
    assert m[:3, 3] == pytest.approx(trans, abs=1e-9)
    assert m[3] == pytest.approx([0, 0, 0, 1])


def test_kabsch_never_returns_a_reflection() -> None:
    src = np.array([[1.0, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]])
    mirrored = src * np.array([-1.0, 1, 1])
    tf = kabsch(src, mirrored)
    assert float(np.linalg.det(tf.rotation)) == pytest.approx(1.0)
    assert tf.rms_m > 0.1  # a mirror image cannot be matched by a rotation


def test_kabsch_rejects_bad_input() -> None:
    line = [(0, 0, 0), (1, 0, 0), (2, 0, 0)]
    with pytest.raises(GeometryError, match="collinear"):
        kabsch(line, line)
    with pytest.raises(GeometryError, match="at least 3"):
        kabsch([(0, 0, 0), (1, 0, 0)], [(0, 0, 0), (1, 0, 0)])
    with pytest.raises(GeometryError, match="differ in shape"):
        kabsch([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)])


@pytest.mark.parametrize("yaw_deg", [-150.0, -30.0, 0.0, 12.0, 90.0, 175.0])
def test_kabsch_yaw_only_recovers_yaw_and_translation(yaw_deg: float) -> None:
    rng = np.random.default_rng(4)
    rot = rot_y(math.radians(yaw_deg))
    trans = np.array([0.4, -0.1, 0.25])
    src = rng.uniform(-0.3, 0.3, size=(4, 3))
    dst = src @ rot.T + trans + rng.normal(scale=0.0003, size=(4, 3))
    tf = kabsch(src, dst, yaw_only=True)
    assert tf.rotation == pytest.approx(rot, abs=3e-3)
    assert tf.translation == pytest.approx(trans, abs=2e-3)
    assert tf.rotation[1] == pytest.approx([0, 1, 0])  # vertical axis untouched


def test_kabsch_yaw_only_two_points_and_degenerate() -> None:
    src = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
    dst = [(1.0, 0.5, 1.0), (1.0, 0.5, 0.0)]  # rotated 90 deg about +Y, then moved
    tf = kabsch(src, dst, yaw_only=True)
    assert tf.apply(src) == pytest.approx(np.asarray(dst), abs=1e-9)
    with pytest.raises(GeometryError, match="degenerate"):
        kabsch([(0, 0, 0), (0, 1, 0)], [(0, 0, 0), (0, 2, 0)], yaw_only=True)


# --- pivot calibration ------------------------------------------------------------------------

TIP = np.array([0.02, -0.03, 0.11])
PIVOT = np.array([0.3, 0.9, -0.4])


def pivot_data(
    rng: np.random.Generator, n: int = 300, max_deg: float = 30.0, noise: float = 0.0003
) -> tuple[Mat, Mat]:
    rots, pos = [], []
    for _ in range(n):
        r = rot_axis_angle(rng.normal(size=3), math.radians(rng.uniform(-max_deg, max_deg)))
        r = r @ rot_axis_angle(rng.normal(size=3), math.radians(rng.uniform(-max_deg, max_deg)))
        rots.append(r)
        pos.append(PIVOT - r @ TIP + rng.normal(scale=noise, size=3))
    return np.array(rots), np.array(pos)


def test_pivot_recovers_tip_and_pivot_point() -> None:
    rots, pos = pivot_data(np.random.default_rng(5))
    res = pivot_calibration(rots, pos)
    assert np.asarray(res.tip_offset) == pytest.approx(TIP, abs=1e-3)
    assert np.asarray(res.pivot_point) == pytest.approx(PIVOT, abs=1e-3)
    assert res.rms_m == pytest.approx(0.0005, abs=0.0003)  # ~ position noise
    assert res.n == 300
    assert res.rotation_spread_deg > 60
    assert res.condition < 20


def test_pivot_rejects_single_axis_rotation() -> None:
    rng = np.random.default_rng(6)
    axis = np.array([0.3, 0.5, 0.8])
    rots = np.array([rot_axis_angle(axis, math.radians(a)) for a in rng.uniform(-45, 45, 200)])
    pos = np.array([PIVOT - r @ TIP for r in rots])
    with pytest.raises(GeometryError, match="poorly conditioned"):
        pivot_calibration(rots, pos)


def test_pivot_rejects_too_little_rotation_and_too_few_samples() -> None:
    rng = np.random.default_rng(7)
    rots, pos = pivot_data(rng, max_deg=2.0)
    with pytest.raises(GeometryError, match="rotated only"):
        pivot_calibration(rots, pos)
    rots, pos = pivot_data(rng, n=10)
    with pytest.raises(GeometryError, match="at least 30"):
        pivot_calibration(rots, pos)
    rots, pos = pivot_data(rng, n=40)
    with pytest.raises(GeometryError, match="rotation matrices"):
        pivot_calibration(rots[:-1], pos)


def test_rotation_spread() -> None:
    rots = np.array([np.eye(3), rot_y(math.radians(40)), rot_y(math.radians(-20))])
    assert rotation_spread_deg(rots) == pytest.approx(60.0, abs=1e-6)
    assert rotation_spread_deg(np.array([np.eye(3)] * 5)) == pytest.approx(0.0, abs=1e-6)
    many = np.array([rot_y(math.radians(a)) for a in np.linspace(-30, 30, 2000)])
    assert rotation_spread_deg(many) == pytest.approx(60.0, abs=0.5)  # subsampled
