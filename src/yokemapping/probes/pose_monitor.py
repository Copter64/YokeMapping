"""Phase 1 pose-stream monitor.

Streams SteamVR device poses for a while and reports per-device update rate, status
changes, and latest position/orientation. Use it to validate Phase 1's exit criteria
(stable pose stream over several minutes, clean recovery from disconnects) and ``--list``
to find the serial of the controller you'll mount on the yoke.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, TextIO

from yokemapping.logging_setup import configure_logging
from yokemapping.tracking.pose import Pose, PoseSample, TrackingStatus, yaw_pitch_roll_degrees
from yokemapping.tracking.steamvr import SteamVRTracker, validate_selector


class Tracker(Protocol):
    def update(self) -> bool: ...
    def sample(self, selector: str) -> PoseSample: ...


@dataclass
class DeviceStats:
    samples: int = 0
    transitions: int = 0
    statuses: Counter[TrackingStatus] = field(default_factory=Counter)
    last_status: TrackingStatus | None = None
    last_pose: Pose | None = None
    interval_ok: int = 0

    @property
    def ok_fraction(self) -> float:
        return self.statuses[TrackingStatus.OK] / self.samples if self.samples else 0.0


def describe_pose(pose: Pose) -> str:
    x, y, z = pose.position
    yaw, pitch, roll = yaw_pitch_roll_degrees(pose.orientation)
    return (
        f"pos=({x:+.3f}, {y:+.3f}, {z:+.3f}) m  "
        f"yaw={yaw:+6.1f} pitch={pitch:+6.1f} roll={roll:+6.1f} deg"
    )


def run_monitor(
    tracker: Tracker,
    selectors: Sequence[str],
    *,
    seconds: float,
    rate_hz: float,
    report_every_s: float = 1.0,
    out: TextIO = sys.stdout,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, DeviceStats]:
    stats = {s: DeviceStats() for s in selectors}
    period = 1.0 / rate_hz
    start = clock()
    next_report = start + report_every_s
    next_tick = start
    while (now := clock()) - start < seconds:
        tracker.update()
        for selector in selectors:
            sample = tracker.sample(selector)
            st = stats[selector]
            st.samples += 1
            st.statuses[sample.status] += 1
            if st.last_status is not None and sample.status is not st.last_status:
                st.transitions += 1
            st.last_status = sample.status
            if sample.pose is not None:
                st.last_pose = sample.pose
                st.interval_ok += 1
        if now >= next_report:
            elapsed = now - (next_report - report_every_s)
            for selector in selectors:
                st = stats[selector]
                rate = st.interval_ok / elapsed if elapsed > 0 else 0.0
                status = st.last_status.value if st.last_status else "-"
                pose = describe_pose(st.last_pose) if st.last_pose else ""
                print(
                    f"[{now - start:6.1f}s] {selector:<10} {status:<19} {rate:5.1f} Hz  {pose}",
                    file=out,
                )
                st.interval_ok = 0
            next_report += report_every_s
        next_tick += period
        sleep(max(0.0, next_tick - clock()))
    return stats


def format_summary(stats: dict[str, DeviceStats], seconds: float) -> str:
    lines = [f"Summary over {seconds:.0f}s:"]
    for selector, st in stats.items():
        breakdown = ", ".join(f"{s.value}={n}" for s, n in st.statuses.most_common())
        lines.append(
            f"  {selector:<10} ok={st.ok_fraction:6.1%}  status changes={st.transitions}  "
            f"samples={st.samples} ({breakdown})"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="yokemapping-pose-monitor",
        description="Stream SteamVR device poses and report rate/status (Phase 1 validation).",
    )
    parser.add_argument(
        "--device",
        action="append",
        type=validate_selector,
        help="device to track: hmd, left, right, or serial:<id> (repeatable; default: hmd)",
    )
    parser.add_argument("--seconds", type=float, default=30.0, help="duration (default: 30)")
    parser.add_argument("--rate", type=float, default=90.0, help="poll rate in Hz (default: 90)")
    parser.add_argument("--list", action="store_true", help="list tracked devices and exit")
    args = parser.parse_args(argv)
    configure_logging(logging.INFO)

    tracker = SteamVRTracker()
    try:
        if args.list:
            if not tracker.update():
                print("SteamVR is not running or not reachable.", file=sys.stderr)
                return 2
            for d in tracker.devices():
                print(
                    f"[{d.index}] {d.device_class:<16} role={d.role or '-':<5} "
                    f"model={d.model} serial={d.serial}"
                )
            return 0
        selectors = args.device or ["hmd"]
        stats = run_monitor(tracker, selectors, seconds=args.seconds, rate_hz=args.rate)
    finally:
        tracker.close()
    print(format_summary(stats, args.seconds))
    return 0 if all(st.statuses[TrackingStatus.OK] for st in stats.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
