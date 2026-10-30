"""Write a printable ChArUco calibration board (for the one-time headset hand-eye calibration).

    uv run --with opencv-python-headless python tools/make_calibration_board.py

Creates data/frames/charuco_board_letter.png (US Letter landscape, 300 dpi). Print it at
100 % / "actual size" (no fit-to-page), stick it on something rigid and flat, and measure one
printed square with calipers (nominal 25.0 mm); pass that value to the solver.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

DPI = 300
MM_PER_IN = 25.4
COLS, ROWS = 8, 6
SQUARE_MM, MARKER_MM = 25.0, 18.0
PAGE_MM = (279.4, 215.9)  # US Letter, landscape


def mm_to_px(mm: float) -> int:
    return round(mm / MM_PER_IN * DPI)


def make_board(
    cols: int = COLS, rows: int = ROWS, square_mm: float = SQUARE_MM, marker_mm: float = MARKER_MM
) -> cv2.aruco.CharucoBoard:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
    return cv2.aruco.CharucoBoard((cols, rows), square_mm, marker_mm, dictionary)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cols", type=int, default=COLS)
    ap.add_argument("--rows", type=int, default=ROWS)
    ap.add_argument("--square-mm", type=float, default=SQUARE_MM)
    ap.add_argument("--out", type=Path, default=Path("data/frames/charuco_board_letter.png"))
    args = ap.parse_args()
    board = make_board(args.cols, args.rows, args.square_mm, args.square_mm * MARKER_MM / SQUARE_MM)
    board_px = (mm_to_px(args.cols * args.square_mm), mm_to_px(args.rows * args.square_mm))
    img = board.generateImage(board_px, marginSize=0, borderBits=1)
    page = np.full((mm_to_px(PAGE_MM[1]), mm_to_px(PAGE_MM[0])), 255, np.uint8)
    y0, x0 = (page.shape[0] - img.shape[0]) // 2, (page.shape[1] - img.shape[1]) // 2
    page[y0 : y0 + img.shape[0], x0 : x0 + img.shape[1]] = img
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.out), page)
    print(
        f"wrote {args.out}: {args.cols}x{args.rows} squares of {args.square_mm} mm "
        f"({args.cols * args.square_mm:.0f} x {args.rows * args.square_mm:.0f} mm), DICT_5X5_100"
    )

    detector = cv2.aruco.CharucoDetector(board)
    _, ids, _, _ = detector.detectBoard(page)
    print(
        f"self-check: detected {0 if ids is None else len(ids)} of "
        f"{(args.cols - 1) * (args.rows - 1)} inner corners in the rendered page"
    )


if __name__ == "__main__":
    main()
