"""Take stereo screenshots of the running VR app through the SteamVR compositor.

Run in your own terminal (needs SteamVR + the VR app running and the headset worn):

    uv run python tools/vr_screenshot_probe.py --shots 5

For each shot it prints where to put your head, waits for Enter, counts down, asks the
compositor for the app's current eye textures (OpenVR IVRScreenshots::TakeStereoScreenshot,
so it works for apps Steam's own overlay does not hook, for example the Microsoft Store
version of MSFS) and records the headset pose over the same time. Output goes to
<out>/<session>/shot_<k>_vr.png (left|right eyes side by side), shot_<k>_hmd_pose.json
(including the OpenVR per-eye projection and eye offsets). The images show the game only.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import openvr

from yokemapping.tracking.steamvr import SteamVRTracker

PROMPTS = [
    "sit normally, looking at the yoke",
    "lean about 15 cm to the LEFT, still looking at the yoke",
    "lean about 15 cm to the RIGHT, still looking at the yoke",
    "lean forward about 10 cm and tilt your head down a little",
    "sit back about 10 cm and turn your head about 15 degrees to the left",
    "turn your head about 15 degrees to the right",
]


def eye_geometry(system: openvr.IVRSystem) -> dict[str, object]:
    out: dict[str, object] = {}
    w, h = system.getRecommendedRenderTargetSize()
    out["recommended_render_size"] = [w, h]
    for name, eye in (("left", openvr.Eye_Left), ("right", openvr.Eye_Right)):
        left, right, top, bottom = system.getProjectionRaw(eye)
        m = system.getEyeToHeadTransform(eye)
        out[name] = {
            "tangents_l_r_t_b": [left, right, top, bottom],
            "eye_to_head": [[m[r][c] for c in range(4)] for r in range(3)],
        }
    return out


def take_shot(tracker: SteamVRTracker, base: Path, countdown: int, geometry: dict) -> bool:
    samples: list[tuple[float, tuple[float, ...], tuple[float, ...]]] = []

    def sample_for(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            tracker.update()
            s = tracker.sample("hmd")
            if s.pose is not None:
                samples.append((time.time(), s.pose.position, s.pose.orientation))
            time.sleep(1 / 90)

    for i in range(countdown, 0, -1):
        print(f"  taking the screenshot in {i} s - hold still")
        sample_for(1.0)
    t_request = time.time()
    try:
        openvr.VRScreenshots().takeStereoScreenshot(str(base) + "_preview", str(base) + "_vr")
    except openvr.error_code.ScreenshotsError as exc:
        print(f"  takeStereoScreenshot failed: {type(exc).__name__}: {exc}")
        return False
    sample_for(3.0)
    # The compositor grabs the frame at the request, so use the pose around that instant.
    poses = [(p, q) for t, p, q in samples if t_request - 0.4 <= t <= t_request + 0.3]
    if not poses or not Path(str(base) + "_vr.png").exists():
        print("  no headset pose or no image; is the headset worn and the game visible?")
        return False
    pos = np.array([p for p, _ in poses])
    quat = np.array([q for _, q in poses])
    quat = quat * np.sign(quat @ quat[0])[:, None]
    mean_q = quat.mean(axis=0)
    mean_q /= np.linalg.norm(mean_q)
    record = {
        "geometry": geometry,
        "hmd_position_m": pos.mean(axis=0).tolist(),
        "hmd_quaternion_wxyz": mean_q.tolist(),
        "hmd_position_std_mm": (pos.std(axis=0) * 1000).tolist(),
        "samples": len(poses),
    }
    Path(str(base) + "_hmd_pose.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"  saved; head moved {max(record['hmd_position_std_mm']):.1f} mm (std) during the shot")
    Path(str(base) + "_preview.png").unlink(missing_ok=True)
    return max(record["hmd_position_std_mm"]) < 4.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("data/frames/vrshots"))
    ap.add_argument("--shots", type=int, default=1)
    ap.add_argument("--countdown", type=int, default=3)
    args = ap.parse_args()
    session = args.out / datetime.now().strftime("session_%Y%m%d-%H%M%S")
    session.mkdir(parents=True, exist_ok=True)

    tracker = SteamVRTracker()
    deadline = time.time() + 15
    while not tracker.update():
        if time.time() > deadline:
            print("SteamVR is not running")
            return 2
        time.sleep(0.3)
    geometry = eye_geometry(openvr.VRSystem())

    k = 0
    while k < args.shots:
        print(f"\nShot {k + 1} of {args.shots}: {PROMPTS[k % len(PROMPTS)]}")
        if input("  Press Enter when in position (q = stop): ").strip().lower() == "q":
            break
        if take_shot(tracker, (session / f"shot_{k + 1}").resolve(), args.countdown, geometry):
            k += 1
        else:
            print("  try this shot again")
    tracker.close()
    print(f"\n{k} shot(s) saved in {session.resolve()}")
    return 0 if k else 1


if __name__ == "__main__":
    raise SystemExit(main())
