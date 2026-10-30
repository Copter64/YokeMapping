"""Calibrate the pixel -> ray mapping of the stereo screenshots (per-eye pinhole intrinsics).

Input: a session from tools/vr_screenshot_probe.py (>= 3 shots from different head positions
and orientations; the virtual world must not move between shots). The headset pose gives the
exact relative camera pose between any two images (and between the two eyes of one shot), so
matched features must satisfy the epipolar constraint F = K2^-T [t]x R K1^-1; the unknown
intrinsics (fx, fy, cx, cy per eye) are fitted to minimise the Sampson error.

    uv run --with opencv-python-headless --with scipy python tools/vr_screenshot_calibrate.py \
        --session data/frames/vrshots/session_<time>
    uv run --with scipy python tools/vr_screenshot_calibrate.py --selftest

Conventions: OpenVR eye space is x right, y up, -z forward; the pinhole camera used here is
x right, y down, z forward (OpenCV), i.e. R_cv = R_eye * diag(1, -1, -1).
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

FLIP = np.diag([1.0, -1.0, -1.0])


def skew(v: np.ndarray) -> np.ndarray:
    return np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])


def intrinsics_matrix(p: np.ndarray) -> np.ndarray:
    return np.array([[p[0], 0, p[2]], [0, p[1], p[3]], [0, 0, 1.0]])


def camera_pose(shot: dict, eye: str) -> tuple[np.ndarray, np.ndarray]:
    """Eye camera orientation (camera->tracking, OpenCV axes) and position, metres."""
    w, x, y, z = shot["hmd_quaternion_wxyz"]
    rot_head = Rotation.from_quat([x, y, z, w]).as_matrix()
    eye_to_head = np.array(shot["geometry"][eye]["eye_to_head"])
    centre = np.array(shot["hmd_position_m"]) + rot_head @ eye_to_head[:, 3]
    return rot_head @ eye_to_head[:, :3] @ FLIP, centre


def fundamental(p1: np.ndarray, p2: np.ndarray, cam1, cam2) -> np.ndarray:
    r1, c1 = cam1
    r2, c2 = cam2
    rel = r2.T @ r1
    t = r2.T @ (c1 - c2)
    k1, k2 = intrinsics_matrix(p1), intrinsics_matrix(p2)
    return np.linalg.inv(k2).T @ skew(t) @ rel @ np.linalg.inv(k1)


def sampson(f: np.ndarray, x1: np.ndarray, x2: np.ndarray) -> np.ndarray:
    h1 = np.c_[x1, np.ones(len(x1))]
    h2 = np.c_[x2, np.ones(len(x2))]
    fx1 = h1 @ f.T
    ftx2 = h2 @ f
    num = np.sum(h2 * fx1, axis=1)
    den = fx1[:, 0] ** 2 + fx1[:, 1] ** 2 + ftx2[:, 0] ** 2 + ftx2[:, 1] ** 2
    return num / np.sqrt(den + 1e-18)


def solve_intrinsics(
    constraints: list[tuple[str, str, tuple, tuple, np.ndarray, np.ndarray]],
    starts: list[dict[str, np.ndarray]],
    tied: bool = True,
) -> tuple[dict[str, np.ndarray], float]:
    """constraints: (eye1, eye2, cam1, cam2, pts1, pts2). Returns intrinsics per eye + median px.

    ``tied`` forces square pixels (fx == fy). The best of several start values is kept.
    """
    eyes = ["left", "right"]
    n = 3 if tied else 4

    def unpack(x: np.ndarray) -> dict[str, np.ndarray]:
        out = {}
        for i, e in enumerate(eyes):
            v = x[n * i : n * i + n]
            out[e] = np.array([v[0], v[0], v[1], v[2]]) if tied else v
        return out

    def pack(p: dict[str, np.ndarray]) -> np.ndarray:
        return np.concatenate([[p[e][0], p[e][2], p[e][3]] if tied else p[e] for e in eyes])

    def resid(x: np.ndarray) -> np.ndarray:
        p = unpack(x)
        return np.concatenate(
            [
                sampson(fundamental(p[e1], p[e2], c1, c2), a, b)
                for e1, e2, c1, c2, a, b in constraints
            ]
        )

    best = None
    for start in starts:
        fit = least_squares(
            resid,
            pack(start),
            loss="soft_l1",
            f_scale=1.5,
            x_scale=np.tile([100, 50, 50] if tied else [100, 100, 50, 50], 2),
        )
        if best is None or fit.cost < best.cost:
            best = fit
    assert best is not None
    return unpack(best.x), float(np.median(np.abs(resid(best.x))))


def ray(p: np.ndarray, cam: tuple[np.ndarray, np.ndarray], pt: np.ndarray):
    rot, centre = cam
    d = rot @ np.linalg.inv(intrinsics_matrix(p)) @ np.array([pt[0], pt[1], 1.0])
    return centre, d / np.linalg.norm(d)


def triangulate(o1, d1, o2, d2) -> tuple[np.ndarray, float]:
    a = np.array([[d1 @ d1, -d1 @ d2], [d1 @ d2, -d2 @ d2]])
    s, t = np.linalg.solve(a, [(o2 - o1) @ d1, (o2 - o1) @ d2])
    p1, p2 = o1 + s * d1, o2 + t * d2
    return (p1 + p2) / 2, float(np.linalg.norm(p1 - p2))


def cross_shot_agreement(shots, intrinsics, stereo_matches, left_pair_matches) -> np.ndarray:
    """3D disagreement (mm) of the same cockpit features triangulated in different shots.

    Features are joined by their exact left-image pixel, so both the stereo and the shot-to-shot
    match sets must come from the same keypoints. Returns one distance per joined feature.
    """
    clouds = []
    for (meta, _, _), (a, b) in zip(shots, stereo_matches, strict=True):
        cl, cr = camera_pose(meta, "left"), camera_pose(meta, "right")
        cloud = {}
        for pa, pb in zip(a, b, strict=True):
            point, miss = triangulate(
                *ray(intrinsics["left"], cl, pa), *ray(intrinsics["right"], cr, pb)
            )
            if miss < 0.003 and 0.25 < np.linalg.norm(point - cl[1]) < 3.0:
                cloud[(round(pa[0], 3), round(pa[1], 3))] = point
        clouds.append(cloud)
    dists = []
    for (i, j), (a, b) in left_pair_matches.items():
        for pa, pb in zip(a, b, strict=True):
            ka, kb = (round(pa[0], 3), round(pa[1], 3)), (round(pb[0], 3), round(pb[1], 3))
            if ka in clouds[i] and kb in clouds[j]:
                dists.append(np.linalg.norm(clouds[i][ka] - clouds[j][kb]) * 1000)
    return np.array(dists)


def selftest() -> int:
    rng = np.random.default_rng(3)
    truth = {
        "left": np.array([748.0, 738.0, 1236.0, 1052.0]),
        "right": np.array([749.0, 739.0, 912.0, 1090.0]),
    }
    shots = []
    for i in range(5):
        rot = Rotation.from_euler(
            "xyz", rng.normal(0, 8, 3) + np.array([10 * i - 20, 5 * i - 10, 0]), degrees=True
        )
        pos = np.array([0.0, 1.9, -0.2]) + rng.normal(0, 0.08, 3)
        q = rot.as_quat()
        shots.append(
            {
                "hmd_quaternion_wxyz": [q[3], q[0], q[1], q[2]],
                "hmd_position_m": pos.tolist(),
                "geometry": {
                    "left": {"eye_to_head": [[1, 0, 0, -0.0328], [0, 1, 0, 0], [0, 0, 1, 0]]},
                    "right": {"eye_to_head": [[1, 0, 0, 0.0328], [0, 1, 0, 0], [0, 0, 1, 0]]},
                },
            }
        )
    world = rng.uniform([-0.5, 1.5, -1.2], [0.5, 2.2, -0.4], (400, 3))

    def project(shot: dict, eye: str) -> np.ndarray:
        r, c = camera_pose(shot, eye)
        pc = (r.T @ (world - c).T).T
        uv = (intrinsics_matrix(truth[eye]) @ pc.T).T
        return uv[:, :2] / uv[:, 2:3] + rng.normal(0, 0.4, (len(world), 2))

    obs = [{e: project(s, e) for e in ("left", "right")} for s in shots]
    cons = []
    for i, s in enumerate(shots):
        cons.append(
            (
                "left",
                "right",
                camera_pose(s, "left"),
                camera_pose(s, "right"),
                obs[i]["left"],
                obs[i]["right"],
            )
        )
    for i, j in itertools.combinations(range(len(shots)), 2):
        for e in ("left", "right"):
            cons.append(
                (e, e, camera_pose(shots[i], e), camera_pose(shots[j], e), obs[i][e], obs[j][e])
            )
    start = {e: truth[e] * np.array([0.93, 0.93, 1.0, 1.0]) + [0, 0, 25, -20] for e in truth}
    est, med = solve_intrinsics(cons, [start], tied=False)
    err = max(float(np.abs(est[e] - truth[e]).max()) for e in truth)
    print(f"selftest: median Sampson {med:.2f} px, worst parameter error {err:.2f} px")
    return 0 if err < 3.0 else 1


def load_session(session: Path) -> list[tuple[dict, np.ndarray, np.ndarray]]:
    import cv2

    shots = []
    for pose_file in sorted(
        session.glob("shot_*_hmd_pose.json"), key=lambda p: int(p.name.split("_")[1])
    ):
        meta = json.loads(pose_file.read_text())
        img = cv2.imread(
            str(pose_file.with_name(pose_file.name.replace("_hmd_pose.json", "_vr.png"))),
            cv2.IMREAD_GRAYSCALE,
        )
        half = img.shape[1] // 2
        shots.append((meta, img[:, :half], img[:, half:]))
    return shots


def match(img1: np.ndarray, img2: np.ndarray, limit: int = 1500) -> tuple[np.ndarray, np.ndarray]:
    import cv2

    sift = cv2.SIFT_create(nfeatures=8000)
    k1, d1 = sift.detectAndCompute(img1, None)
    k2, d2 = sift.detectAndCompute(img2, None)
    flann = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 4}, {"checks": 64})
    good = [m for m, n in flann.knnMatch(d1, d2, k=2) if m.distance < 0.75 * n.distance]
    p1 = np.float32([k1[m.queryIdx].pt for m in good])
    p2 = np.float32([k2[m.trainIdx].pt for m in good])
    if len(p1) < 20:
        return p1[:0], p2[:0]
    _, inl = cv2.findFundamentalMat(p1, p2, cv2.FM_RANSAC, 4.0, 0.999)
    keep = inl.ravel() > 0
    p1, p2 = p1[keep], p2[keep]
    if len(p1) > limit:
        idx = np.random.default_rng(0).choice(len(p1), limit, replace=False)
        p1, p2 = p1[idx], p2[idx]
    return p1.astype(np.float64), p2.astype(np.float64)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--session", type=Path)
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.session:
        ap.error("--session is required (or use --selftest)")

    shots = load_session(args.session)
    if len(shots) < 3:
        print(f"need at least 3 shots, found {len(shots)}")
        return 1
    _, left0, _ = shots[0]
    h, w = left0.shape
    # OpenVR's tangents do not describe what MSFS rendered (tens of px error), so start from a
    # few generic pinhole guesses instead.
    starts = [
        {e: np.array([f, f, w / 2, cy]) for e in ("left", "right")}
        for f in (800.0, 950.0, 1100.0)
        for cy in (h / 2, 0.3 * h)
    ]

    stereo_m, pair_m = [], {}
    cons = []
    for i, (meta, left, right) in enumerate(shots):
        a, b = match(left, right, 100000)
        stereo_m.append((a, b))
        print(f"shot {i + 1}: stereo matches {len(a)}")
        if len(a) >= 30:
            cons.append(
                ("left", "right", camera_pose(meta, "left"), camera_pose(meta, "right"), a, b)
            )
    for (i, s1), (j, s2) in itertools.combinations(enumerate(shots), 2):
        moved = np.linalg.norm(
            np.array(s1[0]["hmd_position_m"]) - np.array(s2[0]["hmd_position_m"])
        )
        if moved < 0.03:
            continue
        for eye, k in (("left", 1), ("right", 2)):
            a, b = match(s1[k], s2[k], 100000)
            print(f"shots {i + 1}-{j + 1} {eye}: {len(a)} matches, head moved {moved * 100:.0f} cm")
            if len(a) >= 30:
                cons.append((eye, eye, camera_pose(s1[0], eye), camera_pose(s2[0], eye), a, b))
                if eye == "left":
                    pair_m[(i, j)] = (a, b)
    fit_cons = []
    rng = np.random.default_rng(0)
    for e1, e2, c1, c2, a, b in cons:
        keep = rng.choice(len(a), min(len(a), 600), replace=False)
        fit_cons.append((e1, e2, c1, c2, a[keep], b[keep]))
    est, med = solve_intrinsics(fit_cons, starts)
    print(f"\nmedian Sampson error {med:.2f} px over {len(cons)} image pairs")
    out = {}
    for e in est:
        fx, fy, cx, cy = est[e]
        print(f"{e}: f {fx:.1f} px  cx {cx:.1f}  cy {cy:.1f}  (image {w}x{h})")
        out[e] = {"fx": fx, "fy": fy, "cx": cx, "cy": cy, "width": w, "height": h}
    dist = cross_shot_agreement(shots, est, stereo_m, pair_m)
    if len(dist):
        print(
            f"cross-shot 3D agreement of {len(dist)} features: median {np.median(dist):.1f} mm, "
            f"90th percentile {np.percentile(dist, 90):.1f} mm"
        )
        out["cross_shot_median_mm"] = float(np.median(dist))
    (args.session / "screenshot_intrinsics.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    print(f"saved {args.session / 'screenshot_intrinsics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
