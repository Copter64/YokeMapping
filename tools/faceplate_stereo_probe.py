"""Locate the Moza MFY faceplate in a pair of headset colour-camera frames (rig frame, mm).

Probe script (not part of the package): needs OpenCV, run with
    uv run --with opencv-python-headless python tools/faceplate_stereo_probe.py \
        --frames data/frames/<run> --ref <faceplate photo> --px-per-mm 4.597 \
        --pair 18 22 --pair 19 23

Method: SIFT features of a straight-on reference photo are matched into each camera's
frame; matched points are undistorted with the colour camera's fisheye (KB) model and a
reference-plane -> undistorted-plane homography is fitted per camera; a grid of reference
points is then triangulated from the two cameras (rig frame, millimetres, from
sensorRigCalibration.json). Buffer ids 18-21 are passthrough_right and 22-25
passthrough_left (ptgrab sorts ids; first half = camera 0), with no extra image rotation.
Reports plane-fit RMS (flatness of the plate, a self-consistency check), the ray miss
distance and the plate centre/normal.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def load_cameras(path: Path) -> dict[str, dict[str, np.ndarray]]:
    cal = json.loads(path.read_text(encoding="utf-8-sig"))
    out = {}
    for cam in cal["cameras"]:
        if not cam["sourceCamera"].startswith("passthrough"):
            continue
        i, e = cam["intrinsics"], cam["extrinsics"]
        x, z = np.array(e["plus_x"]), np.array(e["plus_z"])
        out[cam["sourceCamera"]] = {
            "K": np.array([[i["fx"], 0, i["cx"]], [0, i["fy"], i["cy"]], [0, 0, 1.0]]),
            "D": np.array([i["k1"], i["k2"], i["k3"], i["k4"]]),
            "R": np.column_stack([x, np.cross(z, x), z]),
            "pos": np.array(e["position"]),
        }
    return out


def crop_plate(photo: np.ndarray) -> np.ndarray:
    h, w = photo.shape[:2]
    return cv2.cvtColor(
        photo[int(0.25 * h) : int(0.79 * h), int(0.255 * w) : int(0.725 * w)], cv2.COLOR_BGR2GRAY
    )


def oblique_views(ref: np.ndarray, sift, clahe) -> list[tuple[np.ndarray, np.ndarray]]:
    """SIFT features of ``ref`` and of synthetic oblique views of it (ASIFT-style).

    Returns (keypoints in the ORIGINAL reference pixel frame, descriptors) per view, so
    matches against steeply viewed plates can be pooled in one coordinate system.
    """
    h, w = ref.shape
    views = []
    specs = [(1.0, 0)] + [(t, phi) for t in (1.6, 2.5, 4.0) for phi in (0, 45, 90, 135)]
    for tilt, phi in specs:
        rot = np.vstack([cv2.getRotationMatrix2D((w / 2, h / 2), phi, 1.0), [0, 0, 1]])
        m = np.diag([1.0 / tilt, 1.0, 1.0]) @ rot
        corners = cv2.perspectiveTransform(
            np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2), m
        ).reshape(-1, 2)
        lo = corners.min(axis=0)
        m = np.array([[1, 0, -lo[0]], [0, 1, -lo[1]], [0, 0, 1.0]]) @ m
        size = (
            int(np.ceil(corners[:, 0].max() - lo[0])),
            int(np.ceil(corners[:, 1].max() - lo[1])),
        )
        warped = cv2.warpAffine(ref, m[:2], size, flags=cv2.INTER_AREA)
        kps, desc = sift.detectAndCompute(clahe.apply(warped), None)
        if desc is None:
            continue
        back = cv2.perspectiveTransform(
            np.float32([k.pt for k in kps]).reshape(-1, 1, 2), np.linalg.inv(m)
        ).reshape(-1, 2)
        views.append((back, desc))
    return views


def _match_pooled(views, d, k, flann, ratio=0.8):
    src, dst = [], []
    for ref_pts, desc in views:
        for pair in flann.knnMatch(desc, d, k=2):
            if len(pair) == 2 and pair[0].distance < ratio * pair[1].distance:
                src.append(ref_pts[pair[0].queryIdx])
                dst.append(k[pair[0].trainIdx].pt)
    return np.float32(src).reshape(-1, 2), np.float32(dst).reshape(-1, 2)


def _rectify(im, hom, scale, cam, ref_shape):
    """Warp the fisheye image flat into the reference-plate frame using ``hom``."""
    rh, rw = ref_shape
    gx, gy = np.meshgrid(np.arange(rw, dtype=np.float32), np.arange(rh, dtype=np.float32))
    pts = np.stack([gx.ravel(), gy.ravel()], axis=1).reshape(-1, 1, 2)
    n = cv2.perspectiveTransform(pts, hom).reshape(-1, 1, 2).astype(np.float64) / scale
    pix = cv2.fisheye.distortPoints(n, cam["K"], cam["D"]).reshape(rh, rw, 2).astype(np.float32)
    return cv2.remap(im, pix[..., 0], pix[..., 1], cv2.INTER_LINEAR, borderValue=0)


def fit_undistorted_homography(path, cam, views, sift, clahe, ref_shape, init_hom=None):
    """Fit reference-plate px -> undistorted normalised coords (x scale) for one camera frame.

    Coarse fit from pooled oblique-view matches (or ``init_hom``, a predicted fit), then up to
    three rounds of: warp the image flat into the plate frame with the current fit, re-match
    there (nearly frontal view, where SIFT is reliable) and correct the fit. Falls back to the
    coarse fit if it has enough support on its own.
    """
    im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    k, d = sift.detectAndCompute(clahe.apply(im), None)
    flann = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 4}, {"checks": 64})
    scale = float(cam["K"][0, 0])
    if init_hom is not None:
        hom, coarse_ok = np.array(init_hom, dtype=np.float64), False
    else:
        src, dst = _match_pooled(views, d, k, flann)
        if len(src) < 6:
            raise ValueError(f"only {len(src)} feature matches in {path.name}")
        norm = (
            cv2.fisheye.undistortPoints(
                np.float64(dst).reshape(-1, 1, 2), cam["K"], cam["D"]
            ).reshape(-1, 2)
            * scale
        )
        hom, inl = cv2.findHomography(src, norm.astype(np.float32), cv2.RANSAC, 3.0)
        support = 0 if inl is None else int(inl.sum())
        if hom is None or support < 6:
            raise ValueError(f"only {support} consistent matches in {path.name}")
        coarse_ok = support >= 12
    frontal = [views[0]]
    for _ in range(4 if init_hom is not None else 3):
        flat = _rectify(im, hom, scale, cam, ref_shape)
        kf, df = sift.detectAndCompute(clahe.apply(flat), None)
        if df is None or len(kf) < 8:
            break
        s2, d2 = _match_pooled(frontal, df, kf, flann, ratio=0.85)
        if len(s2) < 8:
            break
        h2, inl2 = cv2.findHomography(s2, d2, cv2.RANSAC, 3.0)
        if h2 is None or inl2.sum() < 20:
            break
        hom = hom @ h2
        coarse_ok = True
        if np.abs(h2 - np.eye(3)).max() < 0.02:
            break
    if not coarse_ok:
        raise ValueError(f"plate could not be confirmed in {path.name}")
    return hom, scale


def triangulate(o1, d1, o2, d2):
    a = np.array([[d1 @ d1, -d1 @ d2], [d1 @ d2, -d2 @ d2]])
    s, t = np.linalg.solve(a, [(o2 - o1) @ d1, (o2 - o1) @ d2])
    p1, p2 = o1 + s * d1, o2 + t * d2
    return (p1 + p2) / 2, float(np.linalg.norm(p1 - p2))


class PlateLocator:
    """Triangulates a grid of faceplate reference points from a right/left frame pair."""

    def __init__(self, ref_photo: np.ndarray, px_per_mm: float, calibration: Path) -> None:
        cams = load_cameras(calibration)
        self.right, self.left = cams["passthrough_right"], cams["passthrough_left"]
        ref = crop_plate(ref_photo)
        rh, rw = ref.shape
        self.clahe = cv2.createCLAHE(3.0, (8, 8))
        self.sift = cv2.SIFT_create(contrastThreshold=0.02)
        self.feats = oblique_views(ref, self.sift, self.clahe)
        self.ref_shape = ref.shape
        self.grid = np.array(
            [[x, y] for y in np.linspace(0, rh, 8) for x in np.linspace(0, rw, 6)], np.float32
        )
        # Plate coordinates in mm: x right, y down the photo, z out of the plate (0).
        self.plane = np.c_[(self.grid - [rw / 2, rh / 2]) / px_per_mm, np.zeros(len(self.grid))]

    def locate(
        self,
        right_frame: Path,
        left_frame: Path,
        inits: tuple[np.ndarray, np.ndarray] | None = None,
    ) -> tuple[np.ndarray, float]:
        """Grid points in the rig frame (mm) and the median ray-miss distance (mm).

        ``inits`` optionally gives predicted (right, left) homographies to start from.
        """
        hs = [
            fit_undistorted_homography(
                f,
                c,
                self.feats,
                self.sift,
                self.clahe,
                self.ref_shape,
                None if inits is None else i,
            )
            for f, c, i in zip(
                (right_frame, left_frame),
                (self.right, self.left),
                inits or (None, None),
                strict=True,
            )
        ]
        na, nb = (
            cv2.perspectiveTransform(self.grid.reshape(-1, 1, 2), h).reshape(-1, 2) / s
            for h, s in hs
        )
        pts, misses = [], []
        for a, b in zip(na, nb, strict=True):
            va, vb = np.array([*a, 1.0]), np.array([*b, 1.0])
            p, miss = triangulate(
                self.right["pos"], self.right["R"] @ (va / np.linalg.norm(va)),
                self.left["pos"], self.left["R"] @ (vb / np.linalg.norm(vb)),
            )  # fmt: skip
            pts.append(p)
            misses.append(miss)
        return np.array(pts), float(np.median(misses))

    def plane_fit(self, pts: np.ndarray) -> tuple[np.ndarray, float, float]:
        """Similarity fit of the plate plane to ``pts``: (rotation, scale, RMS residual mm)."""
        mq, mp = self.plane.mean(0), pts.mean(0)
        u, sv, vt = np.linalg.svd((self.plane - mq).T @ (pts - mp))
        dm = np.diag([1, 1, np.sign(np.linalg.det(vt.T @ u.T))])
        rot = vt.T @ dm @ u.T
        scale = float(np.trace(np.diag(sv) @ dm) / np.sum((self.plane - mq) ** 2))
        resid = np.linalg.norm((scale * (rot @ (self.plane - mq).T).T + mp) - pts, axis=1)
        return rot, scale, float(resid.mean())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=Path, required=True)
    ap.add_argument("--ref", type=Path, required=True)
    ap.add_argument("--px-per-mm", type=float, required=True, help="reference photo scale")
    ap.add_argument(
        "--calibration", type=Path, default=Path("data/frame/sensorRigCalibration.json")
    )
    ap.add_argument(
        "--pair", nargs=2, type=int, action="append", required=True, metavar=("RIGHT_ID", "LEFT_ID")
    )
    args = ap.parse_args()

    loc = PlateLocator(cv2.imread(str(args.ref)), args.px_per_mm, args.calibration)
    for ida, idb in args.pair:
        pts, miss = loc.locate(
            args.frames / f"pt{ida}_pass1.pgm", args.frames / f"pt{idb}_pass1.pgm"
        )
        rot, scale, rms = loc.plane_fit(pts)
        print(
            f"pair {ida}/{idb}: scale {scale:.3f}, plane-fit RMS {rms:.2f} mm, "
            f"ray miss {miss:.2f} mm, centre {pts.mean(0).round(1)} mm, "
            f"normal {rot[:, 2].round(3)}"
        )


if __name__ == "__main__":
    main()
