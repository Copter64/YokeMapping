"""Pick landmarks of the virtual yoke in the stereo screenshots and triangulate them.

    uv run --with opencv-python --with scipy python tools/vr_screenshot_pick.py \
        --session data/frames/vrshots/session_<time> [--landmark bottom_center]

Needs screenshot_intrinsics.json (tools/vr_screenshot_calibrate.py) in the session folder. For
each shot: click the landmark in the LEFT eye (overview), refine it in the zoomed view, then
the RIGHT-eye match is proposed by template matching along the expected row; confirm or click
to correct. Each shot gives a tracking-space point; the mean over shots (and its spread) is
written to landmarks_virtual.json in the registration tool's landmark format.

Keys in every window: Enter = accept, s = skip this shot, q = quit (saves what is done).
``--selftest`` checks the triangulation and matching on the session's first shot without a GUI.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import vr_screenshot_calibrate as vc


def load_intrinsics(session: Path) -> dict[str, np.ndarray]:
    raw = json.loads((session / "screenshot_intrinsics.json").read_text())
    return {
        e: np.array([raw[e]["fx"], raw[e]["fy"], raw[e]["cx"], raw[e]["cy"]])
        for e in raw
        if e in ("left", "right")
    }


def match_right(
    left: np.ndarray, right: np.ndarray, pt: tuple[float, float], intr
) -> tuple[tuple[float, float], float]:
    """Find the right-eye point matching left-eye ``pt`` along the expected row; (point, score)."""
    import cv2

    half = 28
    u, v = round(pt[0]), round(pt[1])
    h, w = left.shape
    if not (half <= u < w - half and half <= v < h - half):
        raise ValueError("landmark too close to the image edge")
    patch = left[v - half : v + half + 1, u - half : u + half + 1]
    v_exp = v + (intr["right"][3] - intr["left"][3])
    v0, v1 = int(max(half, v_exp - 8)), int(min(h - half - 1, v_exp + 8))
    u0, u1 = int(max(half, u - 450)), int(min(w - half - 1, u + 40))
    strip = right[v0 - half : v1 + half + 1, u0 - half : u1 + half + 1]
    res = cv2.matchTemplate(strip, patch, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    x, y = loc
    dx = dy = 0.0
    if 0 < x < res.shape[1] - 1:
        a, b, c = res[y, x - 1], res[y, x], res[y, x + 1]
        dx = 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
    if 0 < y < res.shape[0] - 1:
        a, b, c = res[y - 1, x], res[y, x], res[y + 1, x]
        dy = 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
    return (u0 + x + dx, v0 + y + dy), float(score)


def triangulate_shot(meta: dict, intr, pl, pr) -> tuple[np.ndarray, float]:
    cl, cr = vc.camera_pose(meta, "left"), vc.camera_pose(meta, "right")
    return vc.triangulate(
        *vc.ray(intr["left"], cl, np.array(pl)), *vc.ray(intr["right"], cr, np.array(pr))
    )


def zoom_pick(window: str, img: np.ndarray, centre, half: int = 60, scale: int = 8, mark=None):
    """Show a magnified crop; the user clicks to set the point, Enter accepts. None if skipped."""
    import cv2

    cu, cv_ = round(centre[0]), round(centre[1])
    h, w = img.shape
    u0, v0 = max(0, cu - half), max(0, cv_ - half)
    u1, v1 = min(w, cu + half), min(h, cv_ + half)
    crop = cv2.resize(img[v0:v1, u0:u1], None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    state = {"pt": mark if mark is not None else centre}

    def draw() -> None:
        view = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        x = int((state["pt"][0] - u0) * scale)
        y = int((state["pt"][1] - v0) * scale)
        cv2.line(view, (x - 20, y), (x + 20, y), (0, 255, 255), 1)
        cv2.line(view, (x, y - 20), (x, y + 20), (0, 255, 255), 1)
        cv2.imshow(window, view)

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN:
            state["pt"] = (u0 + x / scale, v0 + y / scale)
            draw()

    cv2.namedWindow(window)
    cv2.setMouseCallback(window, on_mouse)
    draw()
    while True:
        key = cv2.waitKey(30) & 0xFF
        if key in (13, 10):
            cv2.destroyWindow(window)
            return state["pt"]
        if key in (ord("s"), ord("q")):
            cv2.destroyWindow(window)
            return None if key == ord("s") else "quit"


def overview_pick(img: np.ndarray, title: str):
    import cv2

    scale = 1000.0 / max(img.shape)
    small = cv2.cvtColor(
        cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), cv2.COLOR_GRAY2BGR
    )
    state = {"pt": None}

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN:
            state["pt"] = (x / scale, y / scale)
            view = small.copy()
            cv2.circle(view, (x, y), 8, (0, 255, 255), 1)
            cv2.imshow(title, view)

    cv2.namedWindow(title)
    cv2.setMouseCallback(title, on_mouse)
    cv2.imshow(title, small)
    while True:
        key = cv2.waitKey(30) & 0xFF
        if key in (13, 10) and state["pt"] is not None:
            cv2.destroyWindow(title)
            return state["pt"]
        if key in (ord("s"), ord("q")):
            cv2.destroyWindow(title)
            return None if key == ord("s") else "quit"


def selftest(session: Path) -> int:
    intr = load_intrinsics(session)
    shots = vc.load_session(session)
    meta, left, right = shots[0]
    # A strongly textured left-eye point; check the right-eye match and a plausible depth.
    import cv2

    corners = cv2.goodFeaturesToTrack(left[600:1500, 400:1800], 50, 0.05, 40)
    ok = 0
    for c in corners[:, 0]:
        pt = (c[0] + 400, c[1] + 600)
        try:
            pr, score = match_right(left, right, pt, intr)
        except ValueError:
            continue
        if score < 0.8:
            continue
        point, miss = triangulate_shot(meta, intr, pt, pr)
        depth = np.linalg.norm(point - np.array(meta["hmd_position_m"]))
        if 0.2 < depth < 3.0 and miss < 0.004:
            ok += 1
    print(f"selftest: {ok} of {len(corners)} test points matched with sensible depth")
    return 0 if ok >= 10 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=Path, required=True)
    ap.add_argument("--landmark", default="bottom_center")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest(args.session)

    import cv2

    intr = load_intrinsics(args.session)
    results = []
    for k, (meta, left, right) in enumerate(vc.load_session(args.session), start=1):
        print(f"\nShot {k}: click the {args.landmark} of the yoke in the LEFT eye image")
        rough = overview_pick(left, f"shot {k} LEFT eye - click {args.landmark}, Enter")
        if rough == "quit":
            break
        if rough is None:
            continue
        fine = zoom_pick(f"shot {k} LEFT zoom - click exact point, Enter", left, rough)
        if fine == "quit":
            break
        if fine is None:
            continue
        try:
            guess, score = match_right(left, right, fine, intr)
        except ValueError as exc:
            print(f"  {exc}; skipped")
            continue
        print(
            f"  right-eye match proposed with score {score:.2f}"
            + ("  (LOW - check it)" if score < 0.8 else "")
        )
        pr = zoom_pick(
            f"shot {k} RIGHT zoom - proposed match, click to correct, Enter",
            right,
            guess,
            mark=guess,
        )
        if pr == "quit":
            break
        if pr is None:
            continue
        point, miss = triangulate_shot(meta, intr, fine, pr)
        print(f"  tracking-space point (m): {np.round(point, 4)}  ray miss {miss * 1000:.1f} mm")
        results.append(point)
    cv2.destroyAllWindows()
    if not results:
        print("no points picked")
        return 1
    pts = np.array(results)
    mean = pts.mean(axis=0)
    spread = np.linalg.norm(pts - mean, axis=1)
    rms_mm = float(np.sqrt(np.mean(spread**2))) * 1000
    print(
        f"\n{args.landmark}: {len(pts)} shot(s), mean {np.round(mean, 4)} m, "
        f"spread max {spread.max() * 1000:.1f} mm, rms {rms_mm:.1f} mm"
    )
    out = args.session / "landmarks_virtual.json"
    out.write_text(
        json.dumps(
            {
                "kind": "landmarks",
                "target": "virtual",
                "serial": "stereo-screenshots",
                "tip_offset_m": [0.0, 0.0, 0.0],
                "created": datetime.datetime.now(datetime.UTC).isoformat(),
                "landmarks": {
                    args.landmark: {
                        "position": mean.tolist(),
                        "n": len(pts),
                        "spread_m": float(spread.max()),
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
