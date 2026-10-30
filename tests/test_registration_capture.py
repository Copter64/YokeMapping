from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
import pytest
from numpy.typing import NDArray

from tests.test_registration_geometry import rot_axis_angle
from yokemapping.registration.capture import (
    KEY_BACK,
    KEY_CAPTURE,
    KEY_QUIT,
    CapturedPoint,
    CaptureError,
    StillnessCapture,
    capture_landmarks,
    capture_landmarks_on_key,
    collect_poses,
    run_pivot,
    tip_position,
)
from yokemapping.registration.geometry import GeometryError, Vec3
from yokemapping.tracking.pose import Pose, PoseSample, TrackingStatus, quaternion_from_rotation

Mat = NDArray[np.float64]
TIP: Vec3 = (0.02, -0.03, 0.11)
RATE = 90.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 1e-6)


class SimController:
    """A controller whose tip follows ``tip_at(t)`` with Gaussian jitter."""

    def __init__(
        self,
        clock: FakeClock,
        tip_at: Callable[[float], Vec3],
        rot_at: Callable[[float], Mat] = lambda t: np.eye(3),
        *,
        tip_offset: Vec3 = TIP,
        noise_m: float = 0.0005,
        dropout: Callable[[float], bool] = lambda t: False,
        seed: int = 0,
    ) -> None:
        self.clock = clock
        self.tip_at = tip_at
        self.rot_at = rot_at
        self.tip_offset = tip_offset
        self.noise_m = noise_m
        self.dropout = dropout
        self.rng = np.random.default_rng(seed)

    def update(self) -> bool:
        return True

    def sample(self, selector: str) -> PoseSample:
        t = self.clock()
        if self.dropout(t):
            return PoseSample(TrackingStatus.TRACKING_LOST)
        rot = self.rot_at(t)
        tip = np.asarray(self.tip_at(t)) + self.rng.normal(scale=self.noise_m, size=3)
        pos = tip - rot @ np.asarray(self.tip_offset)
        pose = Pose(
            (float(pos[0]), float(pos[1]), float(pos[2])),
            quaternion_from_rotation(rot.tolist()),
            t,
        )
        return PoseSample(TrackingStatus.OK, pose)


def route(
    points: list[Vec3], *, travel_s: float = 0.8, hold_s: float = 2.5, start_s: float = 0.5
) -> Callable[[float], Vec3]:
    """Visit ``points`` in order: travel, then hold still, starting 0.3 m away from the first."""
    first = np.asarray(points[0])
    origin = first + np.array([0.3, 0.0, 0.0])
    stops = [origin] + [np.asarray(p) for p in points]

    def at(t: float) -> Vec3:
        if t < start_s:
            p = origin
        else:
            k = int((t - start_s) // (travel_s + hold_s))
            u = (t - start_s) - k * (travel_s + hold_s)
            if k >= len(points):
                p = stops[-1]
            elif u < travel_s:
                p = stops[k] + (stops[k + 1] - stops[k]) * (u / travel_s)
            else:
                p = stops[k + 1]
        return (float(p[0]), float(p[1]), float(p[2]))

    return at


class Recorder:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.points: dict[str, CapturedPoint] = {}

    def info(self, text: str) -> None:
        self.events.append(f"info:{text[:20]}")

    def prompt(self, name: str) -> None:
        self.events.append(f"prompt:{name}")

    def captured(self, name: str, point: CapturedPoint) -> None:
        self.events.append(f"captured:{name}")
        self.points[name] = point

    def rejected(self, reason: str) -> None:
        self.events.append(f"rejected:{reason}")

    def tick(self) -> None:
        self.events.append("tick")

    def done(self) -> None:
        self.events.append("done")


class KeyScript:
    """A fake keyboard: yields each scheduled key once the clock reaches its time."""

    def __init__(self, clock: FakeClock, schedule: list[tuple[float, str]]) -> None:
        self.clock = clock
        self.pending = list(schedule)

    def __call__(self) -> str | None:
        if self.pending and self.clock() >= self.pending[0][0]:
            return self.pending.pop(0)[1]
        return None


def key_capture(
    clock: FakeClock,
    tip_at: Callable[[float], Vec3],
    schedule: list[tuple[float, str]],
    names: list[str],
    rec: Recorder,
) -> dict[str, CapturedPoint]:
    clock.now = 0.0
    sim = SimController(clock, tip_at, tip_offset=(0.0, 0.0, 0.0))
    return capture_landmarks_on_key(
        sim,
        "right",
        (0.0, 0.0, 0.0),
        names,
        rec,
        KeyScript(clock, schedule),
        settle_s=0.6,
        radius_m=0.004,
        idle_timeout_s=60.0,
        rate_hz=RATE,
        clock=clock,
        sleep=clock.sleep,
    )


def test_key_capture_takes_one_point_per_press() -> None:
    clock, rec = FakeClock(), Recorder()
    a, b = (0.1, 1.0, -0.3), (0.2, 1.1, -0.4)
    result = key_capture(
        clock,
        lambda t: a if t < 5 else b,
        [(1.0, KEY_CAPTURE), (6.0, KEY_CAPTURE)],
        ["shaft_1", "shaft_2"],
        rec,
    )
    assert result["shaft_1"].position == pytest.approx(a, abs=0.002)
    assert result["shaft_2"].position == pytest.approx(b, abs=0.002)
    assert [e for e in rec.events if e.startswith(("prompt", "captured"))] == [
        "prompt:shaft_1",
        "captured:shaft_1",
        "prompt:shaft_2",
        "captured:shaft_2",
    ]
    assert rec.events[-1] == "done"


def test_key_capture_rejects_a_moving_tip_and_waits_for_another_press() -> None:
    clock, rec = FakeClock(), Recorder()
    steady = (0.1, 1.0, -0.3)

    def tip_at(t: float) -> Vec3:
        return (0.1 + 0.03 * math.sin(20 * t), 1.0, -0.3) if t < 3 else steady

    result = key_capture(clock, tip_at, [(1.0, KEY_CAPTURE), (4.0, KEY_CAPTURE)], ["shaft_1"], rec)
    assert any(e.startswith("rejected:the tip moved") for e in rec.events)
    assert result["shaft_1"].position == pytest.approx(steady, abs=0.002)


def test_key_capture_back_redoes_the_previous_landmark() -> None:
    clock, rec = FakeClock(), Recorder()
    first, second = (0.0, 1.0, 0.0), (0.0, 1.0, -0.5)
    result = key_capture(
        clock,
        lambda t: first if t < 8 else second,
        [(1.0, KEY_CAPTURE), (3.0, KEY_BACK), (9.0, KEY_CAPTURE), (11.0, KEY_CAPTURE)],
        ["shaft_1", "shaft_2"],
        rec,
    )
    prompts = [e for e in rec.events if e.startswith("prompt")]
    assert prompts == ["prompt:shaft_1", "prompt:shaft_2", "prompt:shaft_1", "prompt:shaft_2"]
    assert result["shaft_1"].position == pytest.approx(second, abs=0.002)


def test_key_capture_quit_aborts() -> None:
    clock, rec = FakeClock(), Recorder()
    with pytest.raises(CaptureError, match="aborted"):
        key_capture(clock, lambda t: (0.0, 1.0, 0.0), [(1.0, KEY_QUIT)], ["shaft_1"], rec)


def test_key_capture_times_out_without_a_key() -> None:
    clock, rec = FakeClock(), Recorder()
    with pytest.raises(CaptureError, match="timed out"):
        key_capture(clock, lambda t: (0.0, 1.0, 0.0), [], ["shaft_1"], rec)


# --- tip_position -----------------------------------------------------------------------------


def test_tip_position_applies_orientation() -> None:
    rot = rot_axis_angle([0, 1, 0], math.radians(90))
    pose = Pose((1.0, 2.0, 3.0), quaternion_from_rotation(rot.tolist()), 0.0)
    # +z in the controller frame points along +x in the world after a +90 deg yaw
    assert tip_position(pose, (0.0, 0.0, 0.1)) == pytest.approx((1.1, 2.0, 3.0), abs=1e-9)
    assert tip_position(pose, (0.0, 0.0, 0.0)) == pytest.approx((1.0, 2.0, 3.0))


# --- StillnessCapture -------------------------------------------------------------------------


def feed_still(
    det: StillnessCapture, start: float, seconds: float, center: Vec3, noise: float, seed: int = 0
) -> CapturedPoint | None:
    rng = np.random.default_rng(seed)
    t, result = start, None
    while t < start + seconds:
        p = np.asarray(center) + rng.normal(scale=noise, size=3)
        out = det.feed(t, (float(p[0]), float(p[1]), float(p[2])))
        result = result or out
        t += 1 / RATE
    return result


def test_still_tip_is_captured_near_the_true_point() -> None:
    det = StillnessCapture()
    point = feed_still(det, 0.0, 2.0, (0.2, 0.9, -0.4), 0.0005)
    assert point is not None
    assert point.position == pytest.approx((0.2, 0.9, -0.4), abs=0.0005)
    assert point.n >= 80
    assert point.spread_m < 0.003


def test_not_captured_before_the_hold_time() -> None:
    assert feed_still(StillnessCapture(hold_s=1.0), 0.0, 0.7, (0, 0, 0), 0.0002) is None


def test_moving_or_jittery_tip_is_not_captured() -> None:
    det = StillnessCapture()
    t, captured = 0.0, None
    while t < 3.0:  # drifting 20 mm per second
        captured = captured or det.feed(t, (0.02 * t, 0.0, 0.0))
        t += 1 / RATE
    assert captured is None
    assert feed_still(StillnessCapture(), 0.0, 3.0, (0, 0, 0), 0.004, seed=1) is None  # shaky


def test_one_touch_gives_one_point_until_the_tip_moves_away() -> None:
    det = StillnessCapture(rearm_m=0.04)
    assert feed_still(det, 0.0, 1.5, (0, 0, 0), 0.0003) is not None
    assert feed_still(det, 1.5, 3.0, (0.001, 0, 0), 0.0003, seed=2) is None  # still on the point
    assert feed_still(det, 4.5, 2.0, (0.10, 0, 0), 0.0003, seed=3) is not None  # moved away


def test_reset_window_discards_partial_hold() -> None:
    det = StillnessCapture()
    assert feed_still(det, 0.0, 0.6, (0, 0, 0), 0.0002) is None
    det.reset_window()
    assert feed_still(det, 0.6, 0.6, (0, 0, 0), 0.0002, seed=1) is None  # needs a fresh hold


# --- capture_landmarks ------------------------------------------------------------------------

LANDMARKS: dict[str, Vec3] = {
    "shaft_1": (0.0, 0.85, -0.70),
    "shaft_2": (0.0, 0.85, -0.55),
    "left_grip": (-0.17, 0.87, -0.40),
    "right_grip": (0.17, 0.87, -0.40),
}


def run_capture(
    sim: SimController, clock: FakeClock, rec: Recorder, timeout_s: float = 20.0
) -> dict[str, CapturedPoint]:
    return capture_landmarks(
        sim,
        "serial:X",
        TIP,
        list(LANDMARKS),
        rec,
        hold_s=1.0,
        radius_m=0.003,
        timeout_s=timeout_s,
        rate_hz=RATE,
        clock=clock,
        sleep=clock.sleep,
    )


def test_capture_landmarks_collects_every_point() -> None:
    clock = FakeClock()
    clock.now = 0.0
    sim = SimController(
        clock, route(list(LANDMARKS.values())), rot_at=lambda t: rot_axis_angle([0, 1, 0], 0.4)
    )
    rec = Recorder()
    result = run_capture(sim, clock, rec)
    assert list(result) == list(LANDMARKS)
    for name, truth in LANDMARKS.items():
        assert result[name].position == pytest.approx(truth, abs=0.001)
    assert rec.events[0] == "prompt:shaft_1" and rec.events[-1] == "done"
    assert [e for e in rec.events if e.startswith("captured")] == [
        f"captured:{n}" for n in LANDMARKS
    ]


def test_capture_survives_a_tracking_dropout_during_the_hold() -> None:
    clock = FakeClock()
    clock.now = 0.0
    # shaft_1 is held from t=1.3 s; tracking drops at 1.8-2.0 s, so the hold must restart
    sim = SimController(clock, route(list(LANDMARKS.values())), dropout=lambda t: 1.8 < t < 2.0)
    rec = Recorder()
    result = run_capture(sim, clock, rec)
    assert result["shaft_1"].position == pytest.approx(LANDMARKS["shaft_1"], abs=0.001)


def test_capture_times_out_when_the_tip_never_settles() -> None:
    clock = FakeClock()
    clock.now = 0.0
    sim = SimController(clock, lambda t: (0.05 * math.sin(3 * t), 0.9, -0.5))
    rec = Recorder()
    with pytest.raises(CaptureError, match="timed out"):
        run_capture(sim, clock, rec, timeout_s=5.0)
    assert any(e.startswith("rejected") for e in rec.events)


# --- collect_poses and run_pivot --------------------------------------------------------------


def test_collect_poses_counts_valid_samples() -> None:
    clock = FakeClock()
    clock.now = 0.0
    sim = SimController(clock, lambda t: (0.0, 1.0, 0.0), dropout=lambda t: (t * 10) % 10 < 1)
    poses = collect_poses(sim, "left", duration_s=2.0, rate_hz=RATE, clock=clock, sleep=clock.sleep)
    assert 150 < len(poses) < 190  # about 90% valid


def test_collect_poses_rejects_mostly_lost_tracking() -> None:
    clock = FakeClock()
    clock.now = 0.0
    sim = SimController(clock, lambda t: (0.0, 1.0, 0.0), dropout=lambda t: True)
    with pytest.raises(CaptureError, match="valid for only 0 of"):
        collect_poses(sim, "left", duration_s=1.0, rate_hz=RATE, clock=clock, sleep=clock.sleep)


PIVOT = (0.3, 0.9, -0.4)


def wobble(t: float) -> Mat:
    """A smooth rotation about all three axes (about +-40 deg)."""
    return (
        rot_axis_angle([1, 0, 0], 0.7 * math.sin(1.3 * t))
        @ rot_axis_angle([0, 1, 0], 0.6 * math.sin(0.9 * t + 1.0))
        @ rot_axis_angle([0, 0, 1], 0.5 * math.sin(1.7 * t + 2.0))
    )


def test_run_pivot_finds_the_tip_offset() -> None:
    clock = FakeClock()
    clock.now = 0.0
    sim = SimController(clock, lambda t: PIVOT, rot_at=wobble)
    rec = Recorder()
    result = run_pivot(
        sim, "serial:X", rec, delay_s=3, duration_s=10, rate_hz=RATE, clock=clock, sleep=clock.sleep
    )
    assert np.asarray(result.tip_offset) == pytest.approx(TIP, abs=0.001)
    assert np.asarray(result.pivot_point) == pytest.approx(PIVOT, abs=0.001)
    assert result.n > 800
    assert rec.events.count("tick") == 3 + 1 + 1  # countdown, start, end


def test_run_pivot_rejects_a_motionless_controller() -> None:
    clock = FakeClock()
    clock.now = 0.0
    sim = SimController(clock, lambda t: PIVOT)
    with pytest.raises(GeometryError, match="rotated only"):
        run_pivot(
            sim,
            "serial:X",
            Recorder(),
            delay_s=0,
            duration_s=3,
            rate_hz=RATE,
            clock=clock,
            sleep=clock.sleep,
        )
