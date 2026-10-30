"""Capturing touched points and pivot data from a tracked controller.

Hands-free by design: the owner is wearing the headset, so a point is captured when the
controller tip has been still for about a second, and the tool answers with beeps (and
optionally speech) instead of needing the keyboard.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from yokemapping.registration.geometry import PivotResult, Vec3, pivot_calibration, to_vec3
from yokemapping.tracking.pose import Pose, PoseSample, TrackingStatus, rotation_from_quaternion


class CaptureError(RuntimeError):
    """Raised when a capture cannot be completed (timeout, lost tracking, bad data)."""


class PoseSource(Protocol):
    def update(self) -> bool: ...
    def sample(self, selector: str) -> PoseSample: ...


@dataclass(frozen=True)
class CapturedPoint:
    position: Vec3  # tracking space, meters
    n: int  # samples averaged
    spread_m: float  # worst distance of any averaged sample from the mean


def tip_position(pose: Pose, tip_offset: Vec3) -> Vec3:
    """World position of a point fixed in the controller's frame (``tip_offset``, meters)."""
    rot = np.asarray(rotation_from_quaternion(pose.orientation))
    return to_vec3(np.asarray(pose.position) + rot @ np.asarray(tip_offset))


class StillnessCapture:
    """Emits a ``CapturedPoint`` once the tip has stayed within ``radius_m`` for ``hold_s``.

    After a capture it ignores input until the tip has moved ``rearm_m`` away, so one touch
    gives one point.
    """

    def __init__(
        self,
        *,
        hold_s: float = 1.0,
        radius_m: float = 0.003,
        rearm_m: float = 0.04,
        min_samples: int = 30,
    ) -> None:
        self._hold_s = hold_s
        self._radius_m = radius_m
        self._rearm_m = rearm_m
        self._min_samples = min_samples
        self._window: deque[tuple[float, Vec3]] = deque()
        self._anchor: np.ndarray[tuple[int], np.dtype[np.float64]] | None = None

    def reset_window(self) -> None:
        """Forget collected samples (call when tracking drops out); the re-arm anchor stays."""
        self._window.clear()

    def feed(self, t: float, position: Vec3) -> CapturedPoint | None:
        pos = np.asarray(position, dtype=np.float64)
        if self._anchor is not None:
            if float(np.linalg.norm(pos - self._anchor)) < self._rearm_m:
                return None
            self._anchor = None
            self._window.clear()
        self._window.append((t, position))
        while t - self._window[0][0] > self._hold_s:
            self._window.popleft()
        if t - self._window[0][0] < 0.95 * self._hold_s or len(self._window) < self._min_samples:
            return None
        pts = np.asarray([p for _, p in self._window], dtype=np.float64)
        centroid = pts.mean(axis=0)
        spread = float(np.linalg.norm(pts - centroid, axis=1).max())
        if spread > self._radius_m:
            return None
        self._anchor = centroid
        self._window.clear()
        return CapturedPoint(position=to_vec3(centroid), n=len(pts), spread_m=spread)


# --- feedback ---------------------------------------------------------------------------------


class Announcer(Protocol):
    def info(self, text: str) -> None: ...
    def prompt(self, name: str) -> None: ...
    def captured(self, name: str, point: CapturedPoint) -> None: ...
    def rejected(self, reason: str) -> None: ...
    def tick(self) -> None: ...
    def done(self) -> None: ...


_SPEECH_SAFE = re.compile(r"[^A-Za-z0-9 ,.:-]")


class ConsoleAnnouncer:
    """Prints progress, beeps on Windows, and optionally speaks prompts (Windows SAPI)."""

    def __init__(self, *, speak: bool = False, beeps: bool = True) -> None:
        self._speak = speak and sys.platform == "win32"
        self._beeps = beeps and sys.platform == "win32"

    def _beep(self, *tones: tuple[int, int]) -> None:
        if not self._beeps:
            return
        import winsound

        for freq, ms in tones:
            winsound.Beep(freq, ms)

    def _say(self, text: str) -> None:
        if not self._speak:
            return
        clean = _SPEECH_SAFE.sub(" ", text)
        script = (
            "Add-Type -AssemblyName System.Speech; "
            f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak('{clean}')"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def info(self, text: str) -> None:
        print(text, flush=True)

    def prompt(self, name: str) -> None:
        print(f"Touch and hold: {name}", flush=True)
        self._say(f"Touch {name.replace('_', ' ')}")

    def captured(self, name: str, point: CapturedPoint) -> None:
        x, y, z = point.position
        print(
            f"  captured {name}: ({x:+.3f}, {y:+.3f}, {z:+.3f}) m, "
            f"{point.n} samples, spread {point.spread_m * 1000:.1f} mm",
            flush=True,
        )
        self._beep((880, 180))

    def rejected(self, reason: str) -> None:
        print(f"  REJECTED: {reason}", flush=True)
        self._beep((330, 250), (330, 250))

    def tick(self) -> None:
        self._beep((660, 60))

    def done(self) -> None:
        print("Done.", flush=True)
        self._beep((660, 120), (880, 120), (1100, 200))
        self._say("Done")


# --- collection loops -------------------------------------------------------------------------

Clock = Callable[[], float]
Sleep = Callable[[float], None]


def collect_poses(
    source: PoseSource,
    selector: str,
    *,
    duration_s: float,
    rate_hz: float,
    clock: Clock,
    sleep: Sleep,
    min_ok_fraction: float = 0.8,
) -> list[Pose]:
    """Record valid poses of ``selector`` for ``duration_s`` seconds."""
    poses: list[Pose] = []
    total = 0
    start = clock()
    period = 1.0 / rate_hz
    next_tick = start
    while clock() - start < duration_s:
        source.update()
        s = source.sample(selector)
        total += 1
        if s.status is TrackingStatus.OK and s.pose is not None:
            poses.append(s.pose)
        next_tick += period
        sleep(max(0.0, next_tick - clock()))
    if total == 0 or len(poses) / total < min_ok_fraction:
        raise CaptureError(
            f"tracking of {selector} was valid for only {len(poses)} of {total} samples; "
            "check that the controller is on and visible to the headset"
        )
    return poses


@dataclass(frozen=True)
class NoiseResult:
    rms_m: float  # RMS distance of the samples from their mean
    max_m: float  # worst distance from the mean
    n: int
    suggested_radius_m: float  # a stillness radius that comfortably clears this noise


def measure_noise(
    source: PoseSource,
    selector: str,
    *,
    duration_s: float,
    rate_hz: float,
    clock: Clock,
    sleep: Sleep,
) -> NoiseResult:
    """Jitter of a controller that is lying still (its own origin, no tip offset)."""
    poses = collect_poses(
        source, selector, duration_s=duration_s, rate_hz=rate_hz, clock=clock, sleep=sleep
    )
    pts = np.asarray([p.position for p in poses], dtype=np.float64)
    dev = np.linalg.norm(pts - pts.mean(axis=0), axis=1)
    rms = float(np.sqrt(np.mean(dev**2)))
    peak = float(dev.max())
    return NoiseResult(rms, peak, len(pts), max(0.002, 3.0 * peak))


def run_pivot(
    source: PoseSource,
    selector: str,
    announcer: Announcer,
    *,
    delay_s: float,
    duration_s: float,
    rate_hz: float,
    clock: Clock,
    sleep: Sleep,
) -> PivotResult:
    """Countdown, record ``duration_s`` of pivoting, and solve for the tip offset."""
    announcer.info(
        "Pivot calibration: put the controller point you will touch with into a small fixed "
        "divot (or hold it against a fixed corner), then rotate the controller about it in "
        "all directions without letting that point move."
    )
    for remaining in range(int(delay_s), 0, -1):
        announcer.info(f"  starting in {remaining} s")
        announcer.tick()
        sleep(1.0)
    announcer.info(f"  ROTATE NOW for {duration_s:.0f} s")
    announcer.tick()
    poses = collect_poses(
        source, selector, duration_s=duration_s, rate_hz=rate_hz, clock=clock, sleep=sleep
    )
    announcer.tick()
    rotations = [rotation_from_quaternion(p.orientation) for p in poses]
    return pivot_calibration(rotations, [p.position for p in poses])


def capture_landmarks(
    source: PoseSource,
    selector: str,
    tip_offset: Vec3,
    names: Sequence[str],
    announcer: Announcer,
    *,
    hold_s: float,
    radius_m: float,
    timeout_s: float,
    rate_hz: float,
    clock: Clock,
    sleep: Sleep,
) -> dict[str, CapturedPoint]:
    """Capture each named landmark in turn, hands-free (still for ``hold_s`` -> captured)."""
    detector = StillnessCapture(hold_s=hold_s, radius_m=radius_m)
    period = 1.0 / rate_hz
    results: dict[str, CapturedPoint] = {}
    for name in names:
        announcer.prompt(name)
        detector.reset_window()
        deadline = clock() + timeout_s
        while name not in results:
            now = clock()
            if now > deadline:
                announcer.rejected(f"timed out waiting for {name}")
                raise CaptureError(f"timed out after {timeout_s:.0f} s waiting for {name}")
            source.update()
            s = source.sample(selector)
            if s.status is TrackingStatus.OK and s.pose is not None:
                point = detector.feed(now, tip_position(s.pose, tip_offset))
                if point is not None:
                    results[name] = point
                    announcer.captured(name, point)
            else:
                detector.reset_window()
            sleep(period)
    announcer.done()
    return results


LANDMARK_HELP = {
    "shaft_1": "the yoke column/shaft near its base (the part farthest from you)",
    "shaft_2": "the middle of the same column/shaft",
    "shaft_3": "the column/shaft near the hub, closest to you",
    "left_grip": "the centre of the LEFT grip as you sit at the yoke",
    "right_grip": "the centre of the RIGHT grip as you sit at the yoke",
    "top_left": "the LEFT end of the closest-together gap across the top of the yoke "
    "(about 155 mm from the right one)",
    "top_right": "the RIGHT end of that same top gap (about 155 mm from the left one)",
    "bottom_center": "the middle of the bottom edge of the yoke, straight below the top gap",
}

KEY_CAPTURE = "capture"
KEY_BACK = "back"
KEY_QUIT = "quit"


def capture_landmarks_on_key(
    source: PoseSource,
    selector: str,
    tip_offset: Vec3,
    names: Sequence[str],
    announcer: Announcer,
    poll_key: Callable[[], str | None],
    *,
    settle_s: float,
    radius_m: float,
    idle_timeout_s: float,
    rate_hz: float,
    clock: Clock,
    sleep: Sleep,
) -> dict[str, CapturedPoint]:
    """Capture each landmark one step at a time, when ``poll_key`` reports a confirm key.

    ``poll_key`` returns ``KEY_CAPTURE``, ``KEY_BACK`` (redo the previous landmark),
    ``KEY_QUIT`` or None. On a capture key the tip is averaged over ``settle_s`` and the
    point is rejected (same step again) if it moved more than ``radius_m`` meanwhile.
    """
    results: dict[str, CapturedPoint] = {}
    period = 1.0 / rate_hz
    index = 0
    while index < len(names):
        name = names[index]
        announcer.info(
            f"Step {index + 1} of {len(names)}: touch {LANDMARK_HELP.get(name, name)}. "
            "Space/Enter = capture, B = back one step, Q = quit."
        )
        announcer.prompt(name)
        deadline = clock() + idle_timeout_s
        while True:
            if clock() > deadline:
                announcer.rejected(f"no key pressed for {idle_timeout_s:.0f} s")
                raise CaptureError(f"timed out waiting for a key at {name}")
            key = poll_key()
            if key == KEY_QUIT:
                announcer.rejected("quit by user")
                raise CaptureError("capture aborted by the user")
            if key == KEY_BACK:
                index = max(0, index - 1)
                results.pop(names[index], None)
                break
            if key == KEY_CAPTURE:
                point = _average_tip(
                    source, selector, tip_offset, settle_s, rate_hz, clock, sleep, radius_m
                )
                if isinstance(point, str):
                    announcer.rejected(point)
                    continue
                results[name] = point
                announcer.captured(name, point)
                index += 1
                break
            source.update()
            sleep(period)
    announcer.done()
    return results


def _average_tip(
    source: PoseSource,
    selector: str,
    tip_offset: Vec3,
    settle_s: float,
    rate_hz: float,
    clock: Clock,
    sleep: Sleep,
    radius_m: float,
) -> CapturedPoint | str:
    """Average the tip over ``settle_s``; returns a rejection reason string on failure."""
    positions: list[Vec3] = []
    total = 0
    start = clock()
    while clock() - start < settle_s:
        source.update()
        s = source.sample(selector)
        total += 1
        if s.status is TrackingStatus.OK and s.pose is not None:
            positions.append(tip_position(s.pose, tip_offset))
        sleep(1.0 / rate_hz)
    if total == 0 or len(positions) < 0.8 * total or len(positions) < 10:
        return "tracking was lost while capturing; try again"
    pts = np.asarray(positions, dtype=np.float64)
    centroid = pts.mean(axis=0)
    spread = float(np.linalg.norm(pts - centroid, axis=1).max())
    if spread > radius_m:
        return (
            f"the tip moved {spread * 1000:.1f} mm while capturing; hold it still and press again"
        )
    return CapturedPoint(position=to_vec3(centroid), n=len(pts), spread_m=spread)
