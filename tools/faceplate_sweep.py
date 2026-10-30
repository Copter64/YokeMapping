"""Multi-pose colour-camera grab for the headset-to-camera (hand-eye) calibration.

Run from the repo root in your own terminal (needs SteamVR running and the headset worn):

    uv run python tools/faceplate_sweep.py --host <FRAME_IP> --poses 8

For each pose it prints where to put your head, waits for Enter, then (while you hold still)
copies one set of colour-camera frames from the headset into
data/frames/sweep_<time>/pose_<k>/ and stores the averaged headset pose next to them.
The headset-side files are deleted after every grab. The frames show your room: they stay
under the gitignored data/ folder.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from yokemapping.tracking.steamvr import SteamVRTracker

REMOTE_SCRIPT = "/tmp/frame_colour_snapshot.sh"
REMOTE_DIR = "/dev/shm/ptcolour"
PROMPTS = [
    "sit normally, looking straight at the faceplate",
    "lean 15 cm to your LEFT, still looking at the faceplate",
    "lean 15 cm to your RIGHT, still looking at the faceplate",
    "turn your head about 25 degrees to the LEFT (plate still fully in view)",
    "turn your head about 25 degrees to the RIGHT (plate still fully in view)",
    "tilt your head UP about 20 degrees (plate still in view)",
    "tilt your head DOWN about 20 degrees (plate still in view)",
    "roll your head about 15 degrees (ear toward shoulder), plate still in view",
    "lean 10 cm closer to the faceplate",
    "sit back about 10 cm further from the faceplate",
]


class PoseRecorder(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.samples: list[tuple[float, tuple[float, ...], tuple[float, ...]]] = []
        self._halt = threading.Event()
        self.tracker = SteamVRTracker()

    def run(self) -> None:
        while not self._halt.is_set():
            if self.tracker.update():
                s = self.tracker.sample("hmd")
                if s.pose is not None:
                    self.samples.append((time.time(), s.pose.position, s.pose.orientation))
            time.sleep(1 / 90)
        self.tracker.close()

    def stop(self) -> None:
        self._halt.set()

    def window(self, t0: float, t1: float) -> dict[str, object]:
        pts = [s for s in self.samples if t0 <= s[0] <= t1]
        if len(pts) < 20:
            raise RuntimeError(f"only {len(pts)} headset pose samples in the grab window")
        pos = np.array([p for _, p, _ in pts])
        quat = np.array([q for _, _, q in pts])
        quat = quat * np.sign(quat @ quat[0])[:, None]
        mean_q = quat.mean(axis=0)
        mean_q /= np.linalg.norm(mean_q)
        ang = 2 * np.degrees(np.arccos(np.clip(np.abs(quat @ mean_q), -1, 1)))
        return {
            "n": len(pts),
            "position_m": pos.mean(axis=0).tolist(),
            "quaternion_wxyz": mean_q.tolist(),
            "position_std_mm": (pos.std(axis=0) * 1000).tolist(),
            "angle_rms_deg": float(np.sqrt(np.mean(ang**2))),
        }


def ssh_base(host: str, key: Path) -> list[str]:
    return [
        "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
    ]  # fmt: skip


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="frame")
    ap.add_argument("--poses", type=int, default=8)
    ap.add_argument("--key", type=Path, default=Path.home() / ".ssh" / "id_ed25519_frame")
    ap.add_argument("--max-std-mm", type=float, default=3.0)
    args = ap.parse_args()

    target = f"steamos@{args.host}"
    opts = ssh_base(args.host, args.key)
    src = Path(__file__).with_name("frame_colour_snapshot.sh")
    lf = Path(__file__).parent.parent / "data" / "frames" / "_frame_colour_snapshot_lf.sh"
    lf.parent.mkdir(parents=True, exist_ok=True)
    lf.write_bytes(src.read_bytes().replace(b"\r\n", b"\n"))
    out_root = lf.parent / f"sweep_{datetime.now():%Y%m%d-%H%M%S}"
    out_root.mkdir()

    if run(["ssh", *opts, target, "echo connected"]).returncode != 0:
        print(f"Cannot reach {target}; wake the headset and check the address.")
        return 2
    run(["scp", *opts, str(lf), f"{target}:{REMOTE_SCRIPT}"])
    rec = PoseRecorder()
    rec.start()
    time.sleep(2.0)
    if not rec.samples:
        print("No headset pose: is SteamVR running?")
        rec.stop()
        return 2

    k = 0
    try:
        while k < args.poses:
            print(f"\nPose {k + 1} of {args.poses}: {PROMPTS[k % len(PROMPTS)]}")
            reply = input("  Hold still, then press Enter (q = stop here): ").strip().lower()
            if reply == "q":
                break
            time.sleep(0.5)
            t0 = time.time()
            res = run(["ssh", *opts, target, f"SECS=1 bash {REMOTE_SCRIPT}"])
            t1 = time.time()
            pose_dir = out_root / f"pose_{k + 1}"
            pose_dir.mkdir()
            run(["scp", *opts, "-r", f"{target}:{REMOTE_DIR}/*", str(pose_dir)])
            run(["ssh", *opts, target, f"rm -rf {REMOTE_DIR}"])
            (pose_dir / "remote_output.txt").write_text(res.stdout + res.stderr, encoding="utf-8")
            try:
                pose = rec.window(t0, t1)
            except RuntimeError as exc:
                print(f"  {exc}; try this pose again")
                continue
            pose["t_start"], pose["t_end"] = t0, t1
            (pose_dir / "hmd_pose.json").write_text(json.dumps(pose, indent=2), encoding="utf-8")
            frames = len(list(pose_dir.glob("*.pgm")))
            std = max(pose["position_std_mm"])  # type: ignore[type-var]
            print(
                f"  saved {frames} frames; head moved {std:.1f} mm (std), "
                f"{pose['angle_rms_deg']:.2f} deg during the grab"
            )
            if frames == 0:
                print("  no frames: is room view / passthrough ON? try this pose again")
                continue
            if std > args.max_std_mm:
                print("  too much movement; try this pose again (hold still until this prints)")
                continue
            k += 1
    finally:
        rec.stop()
    print(f"\nDone: {k} pose(s) in {out_root}")
    return 0 if k >= 4 else 1


if __name__ == "__main__":
    sys.exit(main())
