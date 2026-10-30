from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from tests.test_registration_capture import (
    PIVOT,
    TIP,
    FakeClock,
    KeyScript,
    Recorder,
    SimController,
    route,
    wobble,
)
from tests.test_registration_geometry import rot_axis_angle, rot_y
from yokemapping.registration import cli
from yokemapping.registration.capture import KEY_CAPTURE
from yokemapping.registration.geometry import Vec3, kabsch
from yokemapping.registration.store import load_alignment, load_landmarks, load_tip
from yokemapping.registration.yoke import LANDMARK_NAMES
from yokemapping.tracking.steamvr import DeviceInfo

LOCAL: dict[str, Vec3] = {
    "shaft_1": (0.0, 0.0, -0.30),
    "shaft_2": (0.0, 0.0, -0.15),
    "shaft_3": (0.0, 0.0, 0.00),
    "left_grip": (-0.17, 0.02, 0.00),
    "right_grip": (0.17, 0.02, 0.00),
}
FIXED_NOW = datetime(2026, 10, 29, 20, 0, 0, tzinfo=UTC)


class FakeTracker(SimController):
    def __init__(self, *args: object, connected: bool = True, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.connected = connected
        self.closed = False

    def update(self) -> bool:
        return self.connected

    def devices(self) -> list[DeviceInfo]:
        return [
            DeviceInfo(0, "HMD", "Deckard MP", "HMD-1", None),
            DeviceInfo(1, "Controller", "Frame Controller", "CTRL-1", "right"),
            DeviceInfo(2, "Controller", "Frame Controller", "CTRL-2", "left"),
        ]

    def close(self) -> None:
        self.closed = True


def place(rot: np.ndarray, trans: Vec3) -> list[Vec3]:
    out = []
    for name in LANDMARK_NAMES:
        w = rot @ np.asarray(LOCAL[name]) + np.asarray(trans)
        out.append((float(w[0]), float(w[1]), float(w[2])))
    return out


def run(
    argv: list[str],
    tracker: FakeTracker | None,
    clock: FakeClock | None = None,
    poll_key: Callable[[], str | None] = lambda: None,
) -> int:
    clock = clock or FakeClock()
    if argv[0] == "capture" and "--confirm" not in argv:
        argv = [*argv, "--confirm", "still"]
    if argv[0] == "capture" and "--layout" not in argv:
        argv = [*argv, "--layout", "column"]
    return cli.main(
        argv,
        tracker_factory=lambda: tracker,  # type: ignore[arg-type, return-value]
        announcer=Recorder(),
        clock=clock,
        sleep=clock.sleep,
        now=lambda: FIXED_NOW,
        poll_key=poll_key,
    )


def pivot_tracker(clock: FakeClock) -> FakeTracker:
    clock.now = 0.0
    return FakeTracker(clock, lambda t: PIVOT, rot_at=wobble)


def capture_tracker(clock: FakeClock, points: list[Vec3]) -> FakeTracker:
    clock.now = 0.0
    return FakeTracker(clock, route(points), rot_at=lambda t: rot_axis_angle([0, 1, 0], 0.3))


def test_devices_lists_controllers(capsys: pytest.CaptureFixture[str]) -> None:
    clock = FakeClock()
    tracker = pivot_tracker(clock)
    assert run(["devices"], tracker, clock) == 0
    out = capsys.readouterr().out
    assert "CTRL-1" in out and "role=right" in out and "HMD" in out
    assert tracker.closed


def test_noise_reports_jitter(capsys: pytest.CaptureFixture[str]) -> None:
    clock = FakeClock()
    clock.now = 0.0
    tracker = FakeTracker(clock, lambda t: (0.0, 1.0, 0.0), noise_m=0.0005)
    assert run(["noise", "--seconds", "5", "--delay", "0"], tracker, clock) == 0
    out = capsys.readouterr().out
    assert "Jitter over" in out and "Suggested --radius-mm" in out and "WARNING" not in out


def test_noise_warns_when_the_controller_is_shaky(capsys: pytest.CaptureFixture[str]) -> None:
    clock = FakeClock()
    clock.now = 0.0
    tracker = FakeTracker(clock, lambda t: (0.0, 1.0, 0.0), noise_m=0.004)
    assert run(["noise", "--seconds", "3", "--delay", "0"], tracker, clock) == 0
    assert "WARNING" in capsys.readouterr().out


def test_two_real_captures_give_a_repeatability_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d = str(tmp_path)
    for seed in (1, 2):
        clock = FakeClock()
        tracker = capture_tracker(clock, place(np.eye(3), (0.0, 0.85, -0.5)))
        tracker.rng = np.random.default_rng(seed)
        out_file = str(tmp_path / f"real_{seed}.json")
        code = run(
            [
                "capture",
                "--target",
                "real",
                "--dir",
                d,
                "--delay",
                "0",
                "--out",
                out_file,
                "--tip-offset-mm",
                "20,-30,110",
            ],
            tracker,
            clock,
        )
        assert code == 0
    capsys.readouterr()
    assert run(["align", str(tmp_path / "real_1.json"), str(tmp_path / "real_2.json")], None) == 0
    out = capsys.readouterr().out
    assert "REPEATABILITY" in out and "Repeatability RMS" in out and "(good)" in out
    assert not list(tmp_path.glob("alignment_*.json"))


def test_pivot_saves_the_tip_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clock = FakeClock()
    code = run(
        ["pivot", "--dir", str(tmp_path), "--delay", "2", "--duration", "10"],
        pivot_tracker(clock),
        clock,
    )
    assert code == 0
    tip = load_tip(tmp_path / "tip_CTRL-1.json")
    assert np.asarray(tip.tip_offset_m) == pytest.approx(TIP, abs=0.001)
    assert tip.serial == "CTRL-1"
    out = capsys.readouterr().out
    assert "Tip offset" in out and "Saved" in out and "WARNING" not in out


def test_pivot_on_a_motionless_controller_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    clock.now = 0.0
    tracker = FakeTracker(clock, lambda t: PIVOT)
    code = run(["pivot", "--dir", str(tmp_path), "--delay", "0", "--duration", "3"], tracker, clock)
    assert code == 2
    assert "rotated only" in capsys.readouterr().err
    assert not (tmp_path / "tip_CTRL-1.json").exists()


def test_capture_needs_a_tip_calibration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    code = run(
        ["capture", "--target", "real", "--dir", str(tmp_path), "--delay", "0"],
        capture_tracker(clock, place(np.eye(3), (0, 0.85, -0.5))),
        clock,
    )
    assert code == 2
    assert "run 'pivot' first" in capsys.readouterr().err


def test_unknown_controller_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clock = FakeClock()
    code = run(
        ["capture", "--target", "real", "--device", "serial:NOPE", "--dir", str(tmp_path)],
        capture_tracker(clock, place(np.eye(3), (0, 0, 0))),
        clock,
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "no controller matches" in err and "CTRL-1" in err


def test_disconnected_steamvr_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    clock = FakeClock()
    tracker = FakeTracker(clock, lambda t: PIVOT, connected=False)
    assert run(["devices"], tracker, clock) == 2
    assert "SteamVR is not running" in capsys.readouterr().err
    assert tracker.closed


def test_full_flow_pivot_capture_real_capture_virtual_align(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d = str(tmp_path)
    clock = FakeClock()
    assert (
        run(["pivot", "--dir", d, "--delay", "1", "--duration", "10"], pivot_tracker(clock), clock)
        == 0
    )

    real_rot, real_pos = rot_axis_angle([0, 1, 0], math.radians(-20)), (0.1, 0.85, -0.55)
    virt_rot, virt_pos = rot_y(math.radians(-12)), (0.16, 0.83, -0.47)
    for target, rot, pos in (("real", real_rot, real_pos), ("virtual", virt_rot, virt_pos)):
        clock = FakeClock()
        tracker = capture_tracker(clock, place(rot, pos))
        assert run(["capture", "--target", target, "--dir", d, "--delay", "0"], tracker, clock) == 0
    out = capsys.readouterr().out
    assert out.count("yoke origin") == 2 and "WARNING" not in out

    real_file = next(tmp_path.glob("real_*.json"))
    virtual_file = next(tmp_path.glob("virtual_*.json"))
    real = load_landmarks(real_file)
    assert list(real.landmarks) == list(LANDMARK_NAMES)
    assert real.landmarks["shaft_2"].position == pytest.approx(
        tuple(real_rot @ np.asarray(LOCAL["shaft_2"]) + np.asarray(real_pos)), abs=0.002
    )

    assert run(["align", str(real_file), str(virtual_file), "--dir", d], None) == 0
    out = capsys.readouterr().out
    assert "Aligned 5 landmarks (full 6-DoF)" in out and "RMS" in out
    aligned = load_alignment(next(tmp_path.glob("alignment_*.json")))
    truth = kabsch(place(real_rot, real_pos), place(virt_rot, virt_pos))
    assert aligned.rotation == pytest.approx(truth.rotation, abs=0.01)
    assert aligned.translation == pytest.approx(tuple(truth.translation), abs=0.004)
    assert aligned.rms_m < 0.003

    assert run(["show", str(real_file)], None) == 0
    assert run(["show", str(next(tmp_path.glob("alignment_*.json")))], None) == 0
    assert run(["show", str(tmp_path / "tip_CTRL-1.json")], None) == 0
    shown = capsys.readouterr().out
    assert "landmarks" in shown and "alignment" in shown and "tip offset" in shown


def test_key_mode_captures_one_step_per_key_press(tmp_path: Path) -> None:
    clock = FakeClock()
    clock.now = 0.0
    points = place(np.eye(3), (0.0, 0.85, -0.5))
    press_times = [2.0 * (k + 1) for k in range(len(points))]

    def tip_at(t: float) -> Vec3:
        k = sum(1 for p in press_times if p <= t)
        return points[max(k - 1, 0)]

    tracker = FakeTracker(clock, tip_at, tip_offset=(0.0, 0.0, 0.0))
    schedule = [(t, KEY_CAPTURE) for t in press_times]
    code = run(
        ["capture", "--target", "real", "--dir", str(tmp_path), "--delay", "0",
         "--confirm", "key", "--tip-offset-mm", "0,0,0"],
        tracker,
        clock,
        poll_key=KeyScript(clock, schedule),
    )  # fmt: skip
    assert code == 0
    saved = load_landmarks(next(tmp_path.glob("real_*.json")))
    for name, expected in zip(LANDMARK_NAMES, points, strict=True):
        assert saved.landmarks[name].position == pytest.approx(expected, abs=0.002)


def test_capture_accepts_an_explicit_tip_offset(tmp_path: Path) -> None:
    clock = FakeClock()
    tracker = capture_tracker(clock, place(np.eye(3), (0.0, 0.85, -0.5)))
    code = run(
        [
            "capture",
            "--target",
            "real",
            "--dir",
            str(tmp_path),
            "--delay",
            "0",
            "--tip-offset-mm",
            "20,-30,110",
        ],
        tracker,
        clock,
    )
    assert code == 0 and list(tmp_path.glob("real_*.json"))


def test_capture_failure_exits_with_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    clock = FakeClock()
    clock.now = 0.0
    tracker = FakeTracker(clock, lambda t: (0.05 * math.sin(3 * t), 0.9, -0.5))
    code = run(
        [
            "capture",
            "--target",
            "real",
            "--dir",
            str(tmp_path),
            "--delay",
            "0",
            "--timeout",
            "4",
            "--tip-offset-mm",
            "20,-30,110",
        ],
        tracker,
        clock,
    )
    assert code == 1
    assert "capture failed" in capsys.readouterr().err
    assert not list(tmp_path.glob("real_*.json"))


def test_align_rejects_swapped_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = str(tmp_path)
    for target in ("real", "virtual"):
        clock = FakeClock()
        tracker = capture_tracker(clock, place(np.eye(3), (0.0, 0.85, -0.5)))
        run(
            [
                "capture",
                "--target",
                target,
                "--dir",
                d,
                "--delay",
                "0",
                "--tip-offset-mm",
                "20,-30,110",
            ],
            tracker,
            clock,
        )
    real_file = next(tmp_path.glob("real_*.json"))
    virtual_file = next(tmp_path.glob("virtual_*.json"))
    assert run(["align", str(virtual_file), str(real_file), "--dir", d], None) == 2
    assert "expected a 'real' then a 'virtual'" in capsys.readouterr().err
    assert run(["align", str(real_file), str(tmp_path / "missing.json"), "--dir", d], None) == 2


def test_bad_tip_offset_argument_is_rejected() -> None:
    with pytest.raises(SystemExit):
        cli.main(["capture", "--target", "real", "--tip-offset-mm", "1,2"])


@pytest.mark.hardware
def test_devices_command_finds_the_controllers_on_real_hardware(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The capture tool can see the headset and controllers through SteamVR.

    Prerequisites: SteamVR running with the Steam Frame connected, both controllers on.
    Procedure: none.
    Pass criteria: exit code 0 and at least one 'Controller' line with a serial.
    """
    code = cli.main(["devices"])
    out = capsys.readouterr().out
    if code == 2:
        pytest.skip("SteamVR not reachable")
    assert code == 0
    assert "Controller" in out and "serial=" in out
