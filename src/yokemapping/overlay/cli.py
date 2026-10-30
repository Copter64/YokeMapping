"""Command line for the SteamVR overlay.

    uv run yokemapping-overlay [--real-pose PATH] [--offset-file PATH]

Run it while SteamVR is up. Open the SteamVR dashboard (Steam button) and pick "YokeMapping":
use the controller laser on the buttons to nudge the ghost onto the virtual yoke's centre, then
Save. The ghost itself shows in the world, at the real yoke's tracked position.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import openvr

from yokemapping.logging_setup import configure_logging
from yokemapping.overlay.app import OverlayApp, SaveAction
from yokemapping.overlay.ghost import GHOST_SIZE_MM, PoseFileError, YokePose, load_real_pose
from yokemapping.overlay.openvr_backend import OpenVROverlayBackend
from yokemapping.overlay.screenshot import StereoShooter
from yokemapping.overlay.state import (
    Offset,
    OffsetFileError,
    OverlayState,
    load_offset,
    save_offset,
)
from yokemapping.tracking.steamvr import SteamVRTracker

log = logging.getLogger(__name__)
DEFAULT_OFFSET = Path("data") / "captures" / "virtual_offset.json"
FRAMES_DIR = Path("data") / "frames"


def latest_handeye(frames_dir: Path = FRAMES_DIR) -> Path | None:
    candidates = sorted(frames_dir.glob("sweep_*/handeye.json"))
    return candidates[-1] if candidates else None


def make_save(path: Path) -> SaveAction:
    def save(offset: Offset, virtual: YokePose) -> str:
        save_offset(
            path,
            offset,
            {
                "virtual_centre_tracking_m": virtual.centre_m.tolist(),
                "virtual_rotation_tracking": virtual.rotation.tolist(),
            },
        )
        return f"Saved offset to {path}"

    return save


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SteamVR overlay to align the real and virtual yoke")
    ap.add_argument("--real-pose", type=Path, help="handeye.json (default: latest sweep)")
    ap.add_argument("--offset-file", type=Path, default=DEFAULT_OFFSET)
    ap.add_argument("--shots-dir", type=Path, default=FRAMES_DIR / "vrshots")
    ap.add_argument("--no-flip-y", action="store_true", help="if buttons react one row off")
    args = ap.parse_args(argv)
    configure_logging(logging.INFO)

    pose_path = args.real_pose or latest_handeye()
    if pose_path is None:
        print("No real-yoke pose found; run tools/faceplate_handeye.py or pass --real-pose")
        return 2
    try:
        real_pose = load_real_pose(pose_path)
        offset = load_offset(args.offset_file) if args.offset_file.exists() else Offset()
    except (PoseFileError, OffsetFileError) as exc:
        print(f"yokemapping-overlay: {exc}", file=sys.stderr)
        return 2

    tracker = SteamVRTracker(connect=lambda: openvr.init(openvr.VRApplication_Overlay))
    deadline = time.time() + 20
    while not tracker.update():
        if time.time() > deadline:
            print("SteamVR is not running", file=sys.stderr)
            return 2
        time.sleep(0.5)

    backend = OpenVROverlayBackend(GHOST_SIZE_MM / 1000.0, flip_y=not args.no_flip_y)
    shooter = StereoShooter(tracker, args.shots_dir)
    app = OverlayApp(
        backend, real_pose, OverlayState(offset), make_save(args.offset_file), shooter.take
    )
    print(
        f"Overlay running (real pose from {pose_path}). Open the SteamVR dashboard > YokeMapping."
    )
    print("Ctrl+C to stop.")
    try:
        while True:
            tracker.update()
            app.step()
            time.sleep(1 / 30)
    except KeyboardInterrupt:
        pass
    finally:
        backend.close()
        tracker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
