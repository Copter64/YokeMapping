"""Stereo screenshot of the running VR app (compositor) plus the headset pose, for calibration.

Writes the same files as ``tools/vr_screenshot_probe.py`` so ``vr_screenshot_calibrate.py`` and
``vr_screenshot_pick.py`` can use them.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import openvr

from yokemapping.tracking.steamvr import SteamVRTracker


def eye_geometry() -> dict[str, Any]:
    system = openvr.VRSystem()
    out: dict[str, Any] = {}
    for name, eye in (("left", openvr.Eye_Left), ("right", openvr.Eye_Right)):
        left, right, top, bottom = system.getProjectionRaw(eye)
        m = system.getEyeToHeadTransform(eye)
        out[name] = {
            "tangents_l_r_t_b": [left, right, top, bottom],
            "eye_to_head": [[m[r][c] for c in range(4)] for r in range(3)],
        }
    return out


class StereoShooter:
    def __init__(
        self,
        tracker: SteamVRTracker,
        out_dir: Path,
        countdown_s: float = 3.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._tracker = tracker
        self._session = out_dir / datetime.now().strftime("overlay_%Y%m%d-%H%M%S")
        self._countdown_s = countdown_s
        self._sleep = sleep
        self._count = 0

    def _sample_for(self, seconds: float, store: list[tuple[float, Any, Any]]) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self._tracker.update()
            s = self._tracker.sample("hmd")
            if s.pose is not None:
                store.append((time.time(), s.pose.position, s.pose.orientation))
            self._sleep(1 / 90)

    def take(self) -> str:
        self._session.mkdir(parents=True, exist_ok=True)
        self._count += 1
        base = (self._session / f"shot_{self._count}").resolve()
        samples: list[tuple[float, Any, Any]] = []
        self._sample_for(self._countdown_s, samples)
        t_request = time.time()
        openvr.VRScreenshots().takeStereoScreenshot(f"{base}_preview", f"{base}_vr")
        self._sample_for(1.5, samples)
        poses = [(p, q) for t, p, q in samples if t_request - 0.4 <= t <= t_request + 0.3]
        image = Path(f"{base}_vr.png")
        if not poses or not image.exists():
            self._count -= 1
            return "No screenshot or headset pose - is the headset on and the game visible?"
        pos = np.array([p for p, _ in poses])
        quat = np.array([q for _, q in poses])
        quat = quat * np.sign(quat @ quat[0])[:, None]
        mean_q = quat.mean(axis=0)
        mean_q /= np.linalg.norm(mean_q)
        std_mm = (pos.std(axis=0) * 1000).tolist()
        record = {
            "geometry": eye_geometry(),
            "hmd_position_m": pos.mean(axis=0).tolist(),
            "hmd_quaternion_wxyz": mean_q.tolist(),
            "hmd_position_std_mm": std_mm,
            "samples": len(poses),
        }
        Path(f"{base}_hmd_pose.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        Path(f"{base}_preview.png").unlink(missing_ok=True)
        return f"Saved shot {self._count} (head moved {max(std_mm):.1f} mm)"
