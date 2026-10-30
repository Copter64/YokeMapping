"""Command line for controller-assisted registration.

    yokemapping-capture devices
    yokemapping-capture pivot   --device right
    yokemapping-capture capture --target real     --device right
    yokemapping-capture capture --target virtual  --device right
    yokemapping-capture align   REAL.json VIRTUAL.json [--yaw-only]
    yokemapping-capture show    FILE.json

Results go to ``data/captures`` (gitignored) unless ``--dir`` says otherwise.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from yokemapping.logging_setup import configure_logging
from yokemapping.registration.capture import (
    KEY_BACK,
    KEY_CAPTURE,
    KEY_QUIT,
    Announcer,
    CaptureError,
    ConsoleAnnouncer,
    PoseSource,
    capture_landmarks,
    capture_landmarks_on_key,
    measure_noise,
    run_pivot,
)
from yokemapping.registration.geometry import GeometryError, RigidTransform, Vec3
from yokemapping.registration.store import (
    CaptureFileError,
    LandmarkFile,
    TipFile,
    file_kind,
    load_alignment,
    load_landmarks,
    load_tip,
    save_alignment,
    save_landmarks,
    save_tip,
)
from yokemapping.registration.yoke import (
    LAYOUTS,
    MFY_NAMES,
    MfyFrame,
    YokeFrame,
    align_landmarks,
    frame_from_landmarks,
    mfy_frame_from_landmarks,
    summarize_transform,
)
from yokemapping.tracking.steamvr import DeviceInfo, SteamVRTracker, validate_selector

DEFAULT_DIR = Path("data") / "captures"
RATE_HZ = 90.0
GOOD_RMS_MM = 3.0
HIGH_RMS_MM = 15.0


class CliError(Exception):
    """A problem the user can fix (reported without a traceback, exit code 2)."""


class Tracker(PoseSource, Protocol):
    def devices(self) -> list[DeviceInfo]: ...
    def close(self) -> None: ...


def _connect(tracker: Tracker, clock: Callable[[], float], sleep: Callable[[float], None]) -> None:
    start = clock()
    while not tracker.update():
        if clock() - start > 15.0:
            raise CliError("SteamVR is not running or the headset is not connected")
        sleep(0.5)


def resolve_controller(tracker: Tracker, device: str) -> tuple[str, str]:
    """Return ``(serial, selector)`` for ``device`` (left, right or serial:<id>)."""
    controllers = [d for d in tracker.devices() if d.device_class == "Controller"]
    for d in controllers:
        by_serial = device.startswith("serial:") and d.serial == device.removeprefix("serial:")
        if (by_serial or d.role == device) and d.serial:
            return d.serial, f"serial:{d.serial}"
    seen = ", ".join(f"{d.serial} ({d.role or 'no role'})" for d in controllers) or "none"
    raise CliError(f"no controller matches {device!r}; connected controllers: {seen}")


def _parse_mm(text: str) -> Vec3:
    try:
        x, y, z = (float(v) / 1000.0 for v in text.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "expected three comma-separated millimetre values"
        ) from exc
    return (x, y, z)


def _stamp(now: Callable[[], datetime]) -> str:
    return now().strftime("%Y%m%d-%H%M%S")


def _iso(now: Callable[[], datetime]) -> str:
    return now().isoformat(timespec="seconds")


def _fmt_mm(v: Vec3) -> str:
    return f"({v[0] * 1000:+.1f}, {v[1] * 1000:+.1f}, {v[2] * 1000:+.1f}) mm"


def _print_frame(frame: YokeFrame) -> None:
    o = frame.origin
    print(f"  yoke origin (tracking space): ({o[0]:+.3f}, {o[1]:+.3f}, {o[2]:+.3f}) m")
    print(
        f"  grip spacing {frame.grip_width_m * 1000:.0f} mm, shaft span "
        f"{frame.shaft_length_m * 1000:.0f} mm, shaft straightness "
        f"{frame.shaft_rms_m * 1000:.1f} mm RMS, grip line {frame.skew_deg:.1f} deg off "
        f"perpendicular, grip midpoint {frame.grip_offset_m * 1000:.0f} mm off the shaft axis"
    )
    for w in frame.warnings():
        print(f"  WARNING: {w}")


def _print_mfy_frame(frame: MfyFrame) -> None:
    o = frame.origin
    print(f"  yoke origin (tracking space): ({o[0]:+.3f}, {o[1]:+.3f}, {o[2]:+.3f}) m")
    print(
        f"  top gap {frame.top_gap_m * 1000:.0f} mm, top-to-bottom {frame.height_m * 1000:.0f} mm, "
        f"grip spacing {frame.grip_width_m * 1000:.0f} mm, bottom-centre "
        f"{frame.skew_deg:.1f} deg off centre"
    )
    for w in frame.warnings():
        print(f"  WARNING: {w}")


def _print_summary(landmarks: dict[str, Vec3]) -> None:
    if all(n in landmarks for n in MFY_NAMES):
        _print_mfy_frame(mfy_frame_from_landmarks(landmarks))
    else:
        _print_frame(frame_from_landmarks(landmarks))


# --- commands ---------------------------------------------------------------------------------


def _cmd_devices(tracker: Tracker, _args: argparse.Namespace) -> int:
    for d in tracker.devices():
        print(
            f"[{d.index}] {d.device_class:<12} role={d.role or '-':<5} "
            f"model={d.model} serial={d.serial}"
        )
    return 0


def _cmd_noise(
    tracker: Tracker,
    args: argparse.Namespace,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> int:
    serial, selector = resolve_controller(tracker, args.device)
    print(f"Controller {serial} ({args.device}): leave it lying still for {args.seconds:.0f} s")
    sleep(args.delay)
    result = measure_noise(
        tracker, selector, duration_s=args.seconds, rate_hz=RATE_HZ, clock=clock, sleep=sleep
    )
    print(
        f"Jitter over {result.n} samples: RMS {result.rms_m * 1000:.2f} mm, worst "
        f"{result.max_m * 1000:.2f} mm. "
        f"Suggested --radius-mm: {result.suggested_radius_m * 1000:.1f}"
    )
    if result.max_m > 0.003:
        print(
            "WARNING: more than 3 mm of jitter at rest; hold-still captures may be slow to trigger."
        )
    return 0


def _cmd_pivot(
    tracker: Tracker,
    args: argparse.Namespace,
    ann: Announcer,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    now: Callable[[], datetime],
) -> int:
    serial, selector = resolve_controller(tracker, args.device)
    print(f"Controller {serial} ({args.device})")
    result = run_pivot(
        tracker,
        selector,
        ann,
        delay_s=args.delay,
        duration_s=args.duration,
        rate_hz=RATE_HZ,
        clock=clock,
        sleep=sleep,
    )
    ann.done()
    print(f"Tip offset (controller frame): {_fmt_mm(result.tip_offset)}")
    print(f"Pivot point (tracking space):  {_fmt_mm(result.pivot_point)}")
    rms_mm = result.rms_m * 1000
    print(
        f"Fit RMS {rms_mm:.2f} mm over {result.n} samples, rotation spread "
        f"{result.rotation_spread_deg:.0f} deg, condition {result.condition:.1f}"
    )
    if rms_mm > GOOD_RMS_MM:
        print(
            f"WARNING: RMS above {GOOD_RMS_MM:.0f} mm; the tip probably slid in the divot. "
            "Repeat the pivot for a more accurate tip."
        )
    path = args.out or args.dir / f"tip_{serial}.json"
    save_tip(
        path,
        TipFile(
            serial, result.tip_offset, result.rms_m, result.n, result.rotation_spread_deg, _iso(now)
        ),
    )
    print(f"Saved {path}")
    return 0


def _resolve_tip(args: argparse.Namespace, serial: str) -> tuple[Vec3, str]:
    if args.tip_offset_mm is not None:
        return args.tip_offset_mm, "given on the command line"
    path = args.tip or args.dir / f"tip_{serial}.json"
    if not path.is_file():
        raise CliError(
            f"no tip calibration for {serial} ({path}); run 'pivot' first, or pass "
            "--tip-offset-mm 0,0,0 to touch with the controller's own origin"
        )
    tip = load_tip(path)
    if tip.serial != serial:
        raise CliError(f"{path} was made for controller {tip.serial}, not {serial}")
    return tip.tip_offset_m, f"from {path}"


def _cmd_capture(
    tracker: Tracker,
    args: argparse.Namespace,
    ann: Announcer,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    now: Callable[[], datetime],
    poll_key: Callable[[], str | None],
) -> int:
    serial, selector = resolve_controller(tracker, args.device)
    names = LAYOUTS[args.layout]
    tip, tip_source = _resolve_tip(args, serial)
    print(f"Controller {serial} ({args.device}); tip offset {_fmt_mm(tip)} {tip_source}")
    where = "the real yoke" if args.target == "real" else "where the virtual yoke appears"
    if args.confirm == "key":
        print(
            f"Step by step on {where}: touch each landmark with the same controller point, "
            "hold it still, then press Space or Enter in this window with your other hand "
            f"to capture it. Order: {', '.join(names)}."
        )
        ann.info(f"Starting in {args.delay:.0f} s")
        sleep(args.delay)
        points = capture_landmarks_on_key(
            tracker,
            selector,
            tip,
            names,
            ann,
            poll_key,
            settle_s=args.settle,
            radius_m=args.radius_mm / 1000.0,
            idle_timeout_s=300.0,
            rate_hz=RATE_HZ,
            clock=clock,
            sleep=sleep,
        )
    else:
        print(
            f"Touch each landmark on {where} with the same controller point you used for the "
            f"pivot and hold still for {args.hold:.1f} s. Order: {', '.join(names)}."
        )
        ann.info(f"Starting in {args.delay:.0f} s")
        sleep(args.delay)
        points = capture_landmarks(
            tracker,
            selector,
            tip,
            names,
            ann,
            hold_s=args.hold,
            radius_m=args.radius_mm / 1000.0,
            timeout_s=args.timeout,
            rate_hz=RATE_HZ,
            clock=clock,
            sleep=sleep,
        )
    path = args.out or args.dir / f"{args.target}_{_stamp(now)}.json"
    save_landmarks(path, LandmarkFile(args.target, serial, tip, _iso(now), points))
    print(f"Saved {path}")
    _print_summary({n: p.position for n, p in points.items()})
    return 0


def _describe(label: str, tf: RigidTransform) -> None:
    s = summarize_transform(tf)
    tx, ty, tz = s.translation_mm
    print(
        f"  {label}: move ({tx:+.1f}, {ty:+.1f}, {tz:+.1f}) mm, yaw {s.yaw_deg:+.2f}, "
        f"pitch {s.pitch_deg:+.2f}, roll {s.roll_deg:+.2f} deg"
    )


def _cmd_align(args: argparse.Namespace, now: Callable[[], datetime]) -> int:
    real, virtual = load_landmarks(args.real), load_landmarks(args.virtual)
    repeat_check = real.target == virtual.target
    if repeat_check:
        print(
            f"Both files are '{real.target}' captures: this is a REPEATABILITY check (the same "
            "yoke touched twice), not a real-to-virtual alignment; nothing is saved."
        )
    elif real.target != "real" or virtual.target != "virtual":
        raise CliError(
            "expected a 'real' then a 'virtual' capture, got "
            f"{real.target!r} and {virtual.target!r}"
        )
    alignment = align_landmarks(
        {n: p.position for n, p in real.landmarks.items()},
        {n: p.position for n, p in virtual.landmarks.items()},
        yaw_only=args.yaw_only,
    )
    tf = alignment.transform
    mode = "yaw only" if args.yaw_only else "full 6-DoF"
    print(f"Aligned {len(alignment.names)} landmarks ({mode}):")
    for name, mm in alignment.residuals_mm().items():
        print(f"  {name:<11} residual {mm:5.1f} mm")
    rms_mm = tf.rms_m * 1000
    print(f"  RMS {rms_mm:.1f} mm")
    print("Real -> virtual (virtual = T * real):")
    _describe("shift of the real yoke onto the virtual one", tf)
    inverse = RigidTransform(
        tf.rotation.T, -tf.rotation.T @ tf.translation, tf.rms_m, tf.residuals_m
    )
    _describe("shift of the virtual yoke onto the real one", inverse)
    if rms_mm > HIGH_RMS_MM:
        print(
            f"WARNING: RMS above {HIGH_RMS_MM:.0f} mm. One or more landmarks were probably touched "
            "at a different place on the virtual yoke than on the real one; recapture."
        )
    if repeat_check:
        verdict = "good" if rms_mm <= GOOD_RMS_MM else "poor; improve the touch technique"
        print(f"Repeatability RMS {rms_mm:.1f} mm ({verdict}).")
        return 0
    path = args.out or args.dir / f"alignment_{_stamp(now)}.json"
    save_alignment(
        path,
        real_file=str(args.real),
        virtual_file=str(args.virtual),
        alignment=alignment,
        created=_iso(now),
    )
    print(f"Saved {path}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    kind = file_kind(args.file)
    print(f"{args.file}: {kind}")
    if kind == "tip":
        tip = load_tip(args.file)
        print(f"  controller {tip.serial}, tip offset {_fmt_mm(tip.tip_offset_m)}")
        print(f"  RMS {tip.rms_m * 1000:.2f} mm over {tip.n} samples, created {tip.created}")
    elif kind == "landmarks":
        data = load_landmarks(args.file)
        print(f"  target {data.target}, controller {data.serial}, created {data.created}")
        for name, p in data.landmarks.items():
            x, y, z = p.position
            print(
                f"  {name:<11} ({x:+.3f}, {y:+.3f}, {z:+.3f}) m  spread {p.spread_m * 1000:.1f} mm"
            )
        _print_summary({n: p.position for n, p in data.landmarks.items()})
    elif kind == "alignment":
        a = load_alignment(args.file)
        print(
            f"  {a.real_file} -> {a.virtual_file}, RMS {a.rms_m * 1000:.1f} mm, created {a.created}"
        )
        for name, mm in a.residuals_mm.items():
            print(f"  {name:<11} residual {mm:5.1f} mm")
    else:
        raise CliError(f"unknown file kind {kind!r}")
    return 0


# --- entry point ------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yokemapping-capture",
        description="Controller-assisted registration of the real yoke to the virtual yoke.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, *, device: bool = True) -> None:
        p.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="output folder (gitignored)")
        p.add_argument(
            "--out", type=Path, help="output file (default: a timestamped name in --dir)"
        )
        if device:
            p.add_argument(
                "--device",
                type=validate_selector,
                default="right",
                help="controller: left, right or serial:<id> (default: right)",
            )
            p.add_argument("--speak", action="store_true", help="also speak prompts (Windows)")

    sub.add_parser("devices", help="list tracked devices and their serials")

    noise = sub.add_parser("noise", help="measure the jitter of a controller lying still")
    noise.add_argument(
        "--device", type=validate_selector, default="right", help="left, right or serial:<id>"
    )
    noise.add_argument("--seconds", type=float, default=10.0)
    noise.add_argument("--delay", type=float, default=3.0, help="seconds before measuring")

    pivot = sub.add_parser("pivot", help="find the controller tip offset by pivoting in a divot")
    common(pivot)
    pivot.add_argument("--delay", type=float, default=10.0, help="seconds before recording")
    pivot.add_argument("--duration", type=float, default=10.0, help="seconds of pivoting")

    capture = sub.add_parser("capture", help="touch the landmarks of a yoke")
    common(capture)
    capture.add_argument("--target", choices=("real", "virtual"), required=True)
    capture.add_argument("--tip", type=Path, help="tip calibration file (default: from --dir)")
    capture.add_argument("--tip-offset-mm", type=_parse_mm, help="tip offset 'x,y,z' in mm")
    capture.add_argument(
        "--delay", type=float, default=10.0, help="seconds before the first prompt"
    )
    capture.add_argument(
        "--layout",
        choices=tuple(LAYOUTS),
        default="mfy",
        help="mfy: butterfly yoke points (default); column: shaft points plus both grips",
    )
    capture.add_argument(
        "--confirm",
        choices=("key", "still"),
        default="key",
        help="key: press Space/Enter for each step (default); still: hold still to capture",
    )
    capture.add_argument("--settle", type=float, default=0.6, help="seconds averaged after a key")
    capture.add_argument("--hold", type=float, default=1.0, help="seconds to hold still")
    capture.add_argument(
        "--radius-mm", type=float, default=3.0, help="max tip movement while holding"
    )
    capture.add_argument("--timeout", type=float, default=90.0, help="seconds allowed per landmark")

    align = sub.add_parser("align", help="align a real capture to a virtual capture")
    common(align, device=False)
    align.add_argument("real", type=Path)
    align.add_argument("virtual", type=Path)
    align.add_argument("--yaw-only", action="store_true", help="assume both yokes are level")

    show = sub.add_parser("show", help="summarise a capture file")
    show.add_argument("file", type=Path)
    return parser


def windows_poll_key() -> str | None:
    """Non-blocking read of the console keyboard; None when no mapped key is pending."""
    if sys.platform != "win32":
        return None
    import msvcrt

    if not msvcrt.kbhit():
        return None
    ch = msvcrt.getwch()
    if ch in (" ", "\r", "\n"):
        return KEY_CAPTURE
    if ch in ("b", "B"):
        return KEY_BACK
    if ch in ("q", "Q", "\x1b"):
        return KEY_QUIT
    return None


def main(
    argv: Sequence[str] | None = None,
    *,
    tracker_factory: Callable[[], Tracker] = SteamVRTracker,
    announcer: Announcer | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    poll_key: Callable[[], str | None] = windows_poll_key,
) -> int:
    args = _parser().parse_args(argv)
    configure_logging(logging.WARNING)
    try:
        if args.command == "align":
            return _cmd_align(args, now)
        if args.command == "show":
            return _cmd_show(args)
        tracker = tracker_factory()
        try:
            _connect(tracker, clock, sleep)
            if args.command == "devices":
                return _cmd_devices(tracker, args)
            if args.command == "noise":
                return _cmd_noise(tracker, args, clock, sleep)
            ann = announcer or ConsoleAnnouncer(speak=args.speak)
            if args.command == "pivot":
                return _cmd_pivot(tracker, args, ann, clock, sleep, now)
            return _cmd_capture(tracker, args, ann, clock, sleep, now, poll_key)
        finally:
            tracker.close()
    except (CliError, CaptureFileError, GeometryError) as exc:
        print(f"yokemapping-capture: {exc}", file=sys.stderr)
        return 2
    except CaptureError as exc:
        print(f"yokemapping-capture: capture failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
