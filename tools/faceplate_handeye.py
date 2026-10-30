"""Headset-to-camera (hand-eye) calibration and faceplate pose in tracking space.

Input: a sweep from tools/faceplate_sweep.py (several head poses looking at the fixed
faceplate). For every pose the faceplate grid is triangulated in the camera-rig frame
(tools/faceplate_stereo_probe.py). The plate does not move, so the same grid points must land
on the same spot in tracking space for every pose:

    p_tracking = R_hmd * (X * p_rig) + t_hmd          (X = rig -> HMD, unknown, 6 DoF)

X is found by minimising how far the points wander between poses. Reports the wander (RMS and
leave-one-pose-out), X, and the faceplate pose in tracking space, and saves handeye.json in the
sweep folder. ``--selftest`` checks the solver on synthetic data.

    uv run --with opencv-python-headless --with scipy python tools/faceplate_handeye.py \
        --sweep data/frames/sweep_<time> --ref data/frames/faceplate_reference_caliper.png \
        --px-per-mm 4.597
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).parent))


def buffer_id(frame: Path) -> int:
    return int(frame.stem[2:].split("_")[0])


def fresh_buffer_ids(pose_dir: Path) -> set[int] | None:
    """Buffers that changed between the grab's two passes (the others hold stale frames).

    Read from the grab log saved by faceplate_sweep.py; None if there is no log.
    """
    log = pose_dir / "remote_output.txt"
    if not log.exists():
        return None
    second_pass = log.read_text(encoding="utf-8", errors="replace").split("pass 1:")[-1]
    return {int(m.group(1)) for m in re.finditer(r"#(\d+) mean [\d.]+ CHANGED", second_pass)}


def fresh_pairs(pose_dir: Path) -> list[tuple[Path, Path]]:
    """(right, left) frame pairs of one grab that are live (non-black) and fresh."""
    import cv2

    frames = sorted(pose_dir.glob("pt*_pass1.pgm"), key=buffer_id)
    half = len(frames) // 2
    live = {f: cv2.imread(str(f), cv2.IMREAD_UNCHANGED).mean() > 1 for f in frames}
    fresh = fresh_buffer_ids(pose_dir)
    return [
        (frames[i], frames[i + half])
        for i in range(half)
        if live[frames[i]]
        and live[frames[i + half]]
        and (fresh is None or {buffer_id(frames[i]), buffer_id(frames[i + half])} <= fresh)
    ]


def predict_inits(
    loc, x_params: np.ndarray, hmd_i: tuple[np.ndarray, np.ndarray], z_track: np.ndarray
) -> tuple[np.ndarray, np.ndarray] | None:
    """Predicted (right, left) plate homographies for a pose from the current estimate."""
    import cv2

    rx = Rotation.from_rotvec(x_params[:3]).as_matrix()
    rh, th = hmd_i
    p_rig = (rx.T @ ((rh.T @ (z_track - th).T) - x_params[3:, None])).T
    out = []
    for cam in (loc.right, loc.left):
        pc = (cam["R"].T @ (p_rig - cam["pos"]).T).T
        if np.any(pc[:, 2] <= 50.0):
            return None
        scale = float(cam["K"][0, 0])
        hom, _ = cv2.findHomography(
            loc.grid, (pc[:, :2] / pc[:, 2:3] * scale).astype(np.float32), 0
        )
        if hom is None:
            return None
        out.append(hom)
    return out[0], out[1]


def hmd_transform(pose: dict[str, object]) -> tuple[np.ndarray, np.ndarray]:
    w, x, y, z = pose["quaternion_wxyz"]  # type: ignore[misc]
    rot = Rotation.from_quat([x, y, z, w]).as_matrix()
    return rot, np.array(pose["position_m"], dtype=np.float64) * 1000.0  # mm


def to_tracking(
    x_params: np.ndarray, hmd: list[tuple[np.ndarray, np.ndarray]], pts: list[np.ndarray]
) -> list[np.ndarray]:
    rx = Rotation.from_rotvec(x_params[:3]).as_matrix()
    return [
        ((rh @ (rx @ p.T + x_params[3:, None])).T + th)
        for (rh, th), p in zip(hmd, pts, strict=False)
    ]


def solve_handeye(
    hmd: list[tuple[np.ndarray, np.ndarray]], pts: list[np.ndarray], starts: int = 60, seed: int = 0
) -> tuple[np.ndarray, float]:
    """Return (rig->HMD params [rotvec, t_mm], RMS wander in mm)."""

    def resid(x: np.ndarray) -> np.ndarray:
        z = np.array(to_tracking(x, hmd, pts))
        return (z - z.mean(axis=0)).ravel()

    rng = np.random.default_rng(seed)
    best = None
    for _ in range(starts):
        x0 = np.concatenate([Rotation.random(random_state=rng).as_rotvec(), rng.normal(0, 100, 3)])
        fit = least_squares(resid, x0, method="lm")
        if best is None or fit.cost < best.cost:
            best = fit
    assert best is not None
    n = len(hmd) * len(pts[0])
    return best.x, float(np.sqrt(2 * best.cost / n * len(hmd) / max(len(hmd) - 1, 1)))


def selftest() -> int:
    rng = np.random.default_rng(1)
    true_x = np.concatenate([Rotation.from_euler("xyz", [170, 5, -8], degrees=True).as_rotvec(),
                             [-45.0, 12.0, 60.0]])  # fmt: skip
    plate = np.array(
        [[x, y, 0.0] for y in np.linspace(-56, 56, 8) for x in np.linspace(-37, 37, 6)]
    )
    plate_rot = Rotation.from_euler("xyz", [-60, 10, 5], degrees=True).as_matrix()
    plate_trk = (plate_rot @ plate.T).T + np.array([300.0, 1400.0, -450.0])
    rx = Rotation.from_rotvec(true_x[:3]).as_matrix()
    hmd, pts = [], []
    for _k in range(8):
        r = Rotation.from_euler(
            "xyz", rng.normal(0, 18, 3) + np.array([20.0, 0.0, 0.0]), degrees=True
        ).as_matrix()
        t = np.array([300.0, 1500.0, -100.0]) + rng.normal(0, 90, 3)
        hmd.append((r, t))
        p_hmd = (r.T @ (plate_trk - t).T).T
        p_rig = (rx.T @ (p_hmd - true_x[3:]).T).T
        pts.append(p_rig + rng.normal(0, 0.6, p_rig.shape))
    est, wander = solve_handeye(hmd, pts)
    rot_err = np.degrees(
        np.linalg.norm(
            Rotation.from_matrix(Rotation.from_rotvec(est[:3]).as_matrix().T @ rx).as_rotvec()
        )
    )
    print(
        f"selftest: wander {wander:.2f} mm, rotation error {rot_err:.2f} deg, "
        f"translation error {np.linalg.norm(est[3:] - true_x[3:]):.2f} mm"
    )
    return 0 if wander < 1.5 and rot_err < 1.0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--sweep", type=Path)
    ap.add_argument("--ref", type=Path)
    ap.add_argument("--px-per-mm", type=float, default=4.597)
    ap.add_argument(
        "--calibration", type=Path, default=Path("data/frame/sensorRigCalibration.json")
    )
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not (args.sweep and args.ref):
        ap.error("--sweep and --ref are required (or use --selftest)")

    import cv2
    from faceplate_stereo_probe import PlateLocator

    loc = PlateLocator(cv2.imread(str(args.ref)), args.px_per_mm, args.calibration)
    pose_dirs = sorted(args.sweep.glob("pose_*"), key=lambda p: int(p.name.split("_")[1]))
    hmd_all = {
        d.name: hmd_transform(json.loads((d / "hmd_pose.json").read_text())) for d in pose_dirs
    }
    pairs_all = {d.name: fresh_pairs(d) for d in pose_dirs}
    measured: dict[str, np.ndarray] = {}

    def measure(name: str, inits_for: np.ndarray | None, x_est: np.ndarray | None) -> None:
        grids = []
        for right_frame, left_frame in pairs_all[name]:
            tag = f"{name} {right_frame.name[:4]}/{left_frame.name[:4]}"
            inits = None
            if inits_for is not None and x_est is not None:
                inits = predict_inits(loc, x_est, hmd_all[name], inits_for)
                if inits is None:
                    print(f"{tag}: plate predicted outside the cameras' view")
                    continue
            try:
                g, miss = loc.locate(right_frame, left_frame, inits)
            except (ValueError, cv2.error) as exc:
                print(f"{tag}: rejected ({exc})")
                continue
            _, scale, rms = loc.plane_fit(g)
            ok = rms < 3.0 and 0.9 < scale < 1.1 and miss < 2.0
            if ok and inits_for is not None and x_est is not None:
                z = to_tracking(x_est, [hmd_all[name]], [g])[0]
                off = float(np.linalg.norm(z.mean(axis=0) - inits_for.mean(axis=0)))
                ok = off < 30.0
                note = f", {off:.1f} mm from prediction"
            else:
                note = ""
            print(
                f"{tag}: plane RMS {rms:.2f} mm, scale {scale:.3f}, miss {miss:.2f} mm{note}"
                f"{'' if ok else '  -> rejected'}"
            )
            if ok:
                grids.append(g)
        if grids:
            measured[name] = np.mean(grids, axis=0)

    for d in pose_dirs:
        measure(d.name, None, None)
    for round_no in range(2):
        if len(measured) < 3:
            break
        names_now = [d.name for d in pose_dirs if d.name in measured]
        x_try, wander_try = solve_handeye(
            [hmd_all[n] for n in names_now], [measured[n] for n in names_now], starts=40
        )
        z_mean = np.mean(
            to_tracking(x_try, [hmd_all[n] for n in names_now], [measured[n] for n in names_now]),
            axis=0,
        )
        missing = [d.name for d in pose_dirs if d.name not in measured]
        print(
            f"\nround {round_no + 1}: {len(names_now)} poses, wander {wander_try:.2f} mm; "
            f"predicting {len(missing)} missing pose(s)\n"
        )
        for name in missing:
            measure(name, z_mean, x_try)
    names = [d.name for d in pose_dirs if d.name in measured]
    hmd = [hmd_all[n] for n in names]
    pts = [measured[n] for n in names]
    skipped = [d.name for d in pose_dirs if d.name not in measured]
    if skipped:
        print(f"\nposes without a usable plate fix: {skipped}")
    if len(hmd) < 4:
        print(f"Only {len(hmd)} usable pose(s); need at least 4 with varied head rotation.")
        return 1

    (args.sweep / "plate_rig_points.json").write_text(
        json.dumps(
            {
                n: {
                    "points_rig_mm": p.tolist(),
                    "hmd_rot": h[0].tolist(),
                    "hmd_pos_mm": h[1].tolist(),
                }
                for n, p, h in zip(names, pts, hmd, strict=True)
            }
        ),
        encoding="utf-8",
    )

    x, wander = solve_handeye(hmd, pts)
    trk = np.array(to_tracking(x, hmd, pts))
    mean = trk.mean(axis=0)
    per_pose = np.sqrt(((trk - mean) ** 2).sum(axis=2).mean(axis=1))
    print(f"\nheadset<-rig transform: rotvec {x[:3].round(4)}, translation {x[3:].round(1)} mm")
    print(
        f"wander of the plate between poses: {wander:.2f} mm RMS; per pose "
        f"{dict(zip(names, per_pose.round(2), strict=False))}"
    )
    loo = []
    for i in range(len(hmd)):
        keep = [j for j in range(len(hmd)) if j != i]
        xi, _ = solve_handeye([hmd[j] for j in keep], [pts[j] for j in keep], starts=20)
        zi = np.array(to_tracking(xi, hmd, pts))
        loo.append(float(np.sqrt(((zi[i] - zi[keep].mean(axis=0)) ** 2).sum(axis=1).mean())))
    print(f"leave-one-pose-out error: {np.round(loo, 2)} mm (mean {np.mean(loo):.2f})")

    rot, scale, _ = loc.plane_fit(mean)
    centre_m = (mean.mean(axis=0) / 1000.0).round(4)
    result = {
        "rig_to_hmd_rotvec": x[:3].tolist(),
        "rig_to_hmd_translation_mm": x[3:].tolist(),
        "wander_rms_mm": wander,
        "leave_one_out_mm": loo,
        "faceplate_centre_tracking_m": centre_m.tolist(),
        "faceplate_rotation_tracking": rot.tolist(),
        "faceplate_scale": scale,
        "poses": names,
    }
    (args.sweep / "handeye.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"faceplate centre in tracking space: {centre_m} m; saved {args.sweep / 'handeye.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
