from __future__ import annotations

import math

import numpy as np
import pytest
from numpy.typing import NDArray

from tests.test_registration_geometry import rot_axis_angle, rot_y
from yokemapping.registration.geometry import GeometryError, Vec3
from yokemapping.registration.yoke import (
    LANDMARK_NAMES,
    MFY_NAMES,
    align_landmarks,
    build_yoke_frame,
    frame_from_landmarks,
    mfy_frame_from_landmarks,
    summarize_transform,
)

Mat = NDArray[np.float64]


def to_tuple(v: Mat) -> Vec3:
    return (float(v[0]), float(v[1]), float(v[2]))


# A yoke in its own frame: +x pilot's right, +y up, +z back toward the pilot.
LOCAL: dict[str, Vec3] = {
    "shaft_1": (0.0, 0.0, -0.30),
    "shaft_2": (0.0, 0.0, -0.15),
    "shaft_3": (0.0, 0.0, 0.00),
    "left_grip": (-0.17, 0.02, 0.00),
    "right_grip": (0.17, 0.02, 0.00),
}


def place(
    landmarks: dict[str, Vec3], rot: Mat, trans: Vec3, noise: float = 0.0, seed: int = 0
) -> dict[str, Vec3]:
    rng = np.random.default_rng(seed)
    out: dict[str, Vec3] = {}
    for name, p in landmarks.items():
        w = rot @ np.asarray(p) + np.asarray(trans) + rng.normal(scale=noise, size=3)
        out[name] = (float(w[0]), float(w[1]), float(w[2]))
    return out


def test_frame_recovers_pose_of_a_noiseless_yoke() -> None:
    rot = rot_axis_angle([0.2, 1.0, 0.1], math.radians(35)) @ rot_axis_angle([1, 0, 0], 0.15)
    trans = (0.4, 0.8, -0.6)
    frame = frame_from_landmarks(place(LOCAL, rot, trans))
    assert frame.rotation == pytest.approx(rot, abs=1e-9)
    # the origin is where the shaft passes the grip midpoint: (0, 0, 0) plus a 20 mm y offset
    # perpendicular to the shaft is removed by projecting onto the shaft axis
    assert frame.origin == pytest.approx(trans, abs=1e-9)
    assert frame.grip_width_m == pytest.approx(0.34)
    assert frame.grip_offset_m == pytest.approx(0.02)
    assert frame.shaft_length_m == pytest.approx(0.30)
    assert frame.shaft_rms_m == pytest.approx(0, abs=1e-12)
    assert frame.skew_deg == pytest.approx(0, abs=1e-9)
    assert float(np.linalg.det(frame.rotation)) == pytest.approx(1.0)
    assert frame.matrix()[:3, 3] == pytest.approx(trans)


def test_frame_axes_have_the_documented_meaning() -> None:
    frame = frame_from_landmarks(LOCAL)  # identity placement
    assert frame.rotation == pytest.approx(np.eye(3), abs=1e-12)
    # hub nearer the pilot than the base: z points toward the hub end of the shaft
    reversed_order = {**LOCAL, "shaft_1": LOCAL["shaft_3"], "shaft_3": LOCAL["shaft_1"]}
    flipped = frame_from_landmarks(reversed_order)
    assert flipped.rotation[:, 2] == pytest.approx([0, 0, -1], abs=1e-12)


def test_frame_with_noise_stays_close() -> None:
    rot = rot_axis_angle([0, 1, 0], math.radians(-50))
    frame = frame_from_landmarks(place(LOCAL, rot, (0.1, 0.9, -0.5), noise=0.002, seed=3))
    angle_error = math.degrees(
        math.acos(np.clip((np.trace(rot.T @ frame.rotation) - 1) / 2, -1, 1))
    )
    assert angle_error < 2.0
    assert frame.shaft_rms_m < 0.005
    assert frame.warnings() == []


def test_frame_warnings_flag_bad_captures() -> None:
    sloppy = dict(LOCAL)
    sloppy["shaft_2"] = (0.03, 0.0, -0.15)
    sloppy["left_grip"] = (-0.05, 0.02, 0.10)
    frame = frame_from_landmarks(sloppy)
    text = " ".join(frame.warnings())
    assert "off a straight line" in text
    assert "perpendicular" in text
    assert "off the shaft axis" in text
    assert "spacing" not in text  # 240 mm is a plausible yoke width

    narrow = {**LOCAL, "left_grip": (-0.03, 0.02, 0.0), "right_grip": (0.03, 0.02, 0.0)}
    assert any("spacing" in w for w in frame_from_landmarks(narrow).warnings())
    short = {**LOCAL, "shaft_1": (0.0, 0.0, -0.03), "shaft_2": (0.0, 0.0, -0.015)}
    assert any("span only" in w for w in frame_from_landmarks(short).warnings())


def test_frame_requires_valid_landmarks() -> None:
    with pytest.raises(GeometryError, match="missing"):
        frame_from_landmarks({k: v for k, v in LOCAL.items() if k != "right_grip"})
    with pytest.raises(GeometryError, match="two shaft"):
        frame_from_landmarks({k: v for k, v in LOCAL.items() if k not in ("shaft_1", "shaft_2")})
    with pytest.raises(GeometryError, match="coincide"):
        build_yoke_frame([(0, 0, -0.3), (0, 0, 0)], (0.1, 0, 0), (0.1, 0, 0))
    with pytest.raises(GeometryError, match="parallel"):
        build_yoke_frame([(0, 0, -0.3), (0, 0, 0)], (0, 0, -0.1), (0, 0, 0.1))


def test_alignment_recovers_known_offset() -> None:
    real = place(LOCAL, np.eye(3), (0.0, 0.8, -0.5), noise=0.001, seed=1)
    moved = place(LOCAL, rot_y(math.radians(8)), (0.05, 0.78, -0.43), noise=0.001, seed=2)
    result = align_landmarks(real, moved)
    summary = summarize_transform(result.transform)
    assert summary.yaw_deg == pytest.approx(8.0, abs=1.0)
    assert summary.translation_mm[0] == pytest.approx(
        1000 * (0.05 - (math.cos(math.radians(8)) * 0.0 + math.sin(math.radians(8)) * -0.5)), abs=15
    )
    assert result.transform.rms_m < 0.003
    assert set(result.residuals_mm()) == set(LANDMARK_NAMES)
    assert result.names == LANDMARK_NAMES


def test_alignment_yaw_only_matches_full_for_level_yokes() -> None:
    real = place(LOCAL, np.eye(3), (0.0, 0.8, -0.5), noise=0.0005, seed=4)
    moved = place(LOCAL, rot_y(math.radians(-12)), (0.1, 0.8, -0.4), noise=0.0005, seed=5)
    full = summarize_transform(align_landmarks(real, moved).transform)
    yaw = summarize_transform(align_landmarks(real, moved, yaw_only=True).transform)
    assert yaw.yaw_deg == pytest.approx(full.yaw_deg, abs=1.0)
    assert yaw.pitch_deg == pytest.approx(0.0, abs=1e-6)
    assert yaw.roll_deg == pytest.approx(0.0, abs=1e-6)


def test_alignment_uses_only_common_landmarks_and_needs_enough() -> None:
    real = place(LOCAL, np.eye(3), (0, 0, 0))
    partial = {k: v for k, v in real.items() if k in ("shaft_3", "left_grip", "right_grip")}
    result = align_landmarks(real, partial)
    assert result.names == ("shaft_3", "left_grip", "right_grip")
    with pytest.raises(GeometryError, match="at least 3"):
        align_landmarks(real, {"shaft_3": real["shaft_3"], "left_grip": real["left_grip"]})
    assert align_landmarks(
        real, {"shaft_3": real["shaft_3"], "left_grip": real["left_grip"]}, yaw_only=True
    ).names == ("shaft_3", "left_grip")


MFY_LOCAL: dict[str, Vec3] = {
    "top_left": (-0.0775, 0.0, 0.0),
    "top_right": (0.0775, 0.0, 0.0),
    "bottom_center": (0.0, -0.17, 0.02),
    "left_grip": (-0.19, -0.05, 0.03),
    "right_grip": (0.19, -0.05, 0.03),
}


def test_mfy_frame_reads_gap_height_and_orientation() -> None:
    rot, trans = rot_y(math.radians(25)), np.array([0.3, 1.4, -0.6])
    pts = {n: to_tuple(rot @ np.asarray(p) + trans) for n, p in MFY_LOCAL.items()}
    frame = mfy_frame_from_landmarks(pts)
    assert frame.top_gap_m == pytest.approx(0.155)
    assert frame.grip_width_m == pytest.approx(0.38)
    assert frame.height_m == pytest.approx(math.hypot(0.17, 0.02))
    assert frame.rotation[:, 0] == pytest.approx(rot[:, 0], abs=1e-9)
    assert frame.origin == pytest.approx(tuple(trans), abs=1e-9)
    assert frame.warnings() == []


def test_mfy_frame_warns_when_the_top_gap_is_not_155_mm() -> None:
    pts = dict(MFY_LOCAL)
    pts["top_left"] = (-0.12, 0.0, 0.0)
    assert any("top gap" in w for w in mfy_frame_from_landmarks(pts).warnings())


def test_mfy_frame_needs_all_points() -> None:
    pts = dict(MFY_LOCAL)
    del pts["bottom_center"]
    with pytest.raises(GeometryError, match="bottom_center"):
        mfy_frame_from_landmarks(pts)


def test_alignment_works_with_the_mfy_layout() -> None:
    rot, trans = rot_y(math.radians(-8)), np.array([0.02, -0.01, 0.03])
    real = dict(MFY_LOCAL)
    virtual = {n: to_tuple(rot @ np.asarray(p) + trans) for n, p in real.items()}
    alignment = align_landmarks(real, virtual)
    assert set(alignment.names) == set(MFY_NAMES)
    assert alignment.transform.rms_m < 1e-9
