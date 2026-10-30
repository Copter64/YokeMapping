"""Pure geometry for controller-assisted registration. No hardware, no I/O.

Units are meters and the coordinate system is OpenVR's tracking space: right-handed,
+X right, +Y up, -Z forward. Matrices are NumPy arrays; values handed back to callers
that are stored or printed are plain tuples of floats.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Vec3 = tuple[float, float, float]
Mat = NDArray[np.float64]

_EPS = 1e-9


class GeometryError(ValueError):
    """Raised when the input cannot determine a unique, well-conditioned answer."""


def as_points(points: Sequence[Sequence[float]] | Mat, *, minimum: int = 1) -> Mat:
    arr = np.asarray(points, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] != 3:
        raise GeometryError(f"expected an (N, 3) array of points, got shape {arr.shape}")
    if arr.shape[0] < minimum:
        raise GeometryError(f"need at least {minimum} point(s), got {arr.shape[0]}")
    if not np.all(np.isfinite(arr)):
        raise GeometryError("points contain NaN or infinity")
    return arr


def to_vec3(a: Mat) -> Vec3:
    return (float(a[0]), float(a[1]), float(a[2]))


# --- lines ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    point: Vec3  # a point on the line (the centroid of the fitted points)
    direction: Vec3  # unit vector
    rms_m: float  # RMS perpendicular distance of the fitted points to the line
    max_m: float  # worst perpendicular distance
    n: int


def fit_line(
    points: Sequence[Sequence[float]] | Mat, *, direction_hint: Vec3 | None = None
) -> Line:
    """Total-least-squares line through ``points`` (at least 2).

    The sign of the direction is arbitrary unless ``direction_hint`` is given, in which
    case it is flipped to point the same way as the hint.
    """
    pts = as_points(points, minimum=2)
    centroid = pts.mean(axis=0)
    centered = pts - centroid
    _, s, vt = np.linalg.svd(centered, full_matrices=False)
    if s[0] < _EPS:
        raise GeometryError("points coincide; cannot fit a line")
    direction = vt[0]
    if direction_hint is not None and float(direction @ np.asarray(direction_hint)) < 0:
        direction = -direction
    along = centered @ direction
    perp = np.linalg.norm(centered - np.outer(along, direction), axis=1)
    return Line(
        point=to_vec3(centroid),
        direction=to_vec3(direction),
        rms_m=float(np.sqrt(np.mean(perp**2))),
        max_m=float(perp.max()),
        n=len(pts),
    )


def closest_point_on_line(line: Line, point: Vec3) -> Vec3:
    p0 = np.asarray(line.point)
    d = np.asarray(line.direction)
    return to_vec3(p0 + d * float((np.asarray(point) - p0) @ d))


def distance_to_line(line: Line, point: Vec3) -> float:
    return float(np.linalg.norm(np.asarray(point) - np.asarray(closest_point_on_line(line, point))))


# --- rigid alignment --------------------------------------------------------------------------


@dataclass(frozen=True, eq=False)
class RigidTransform:
    """``target = rotation @ source + translation`` fitted to a set of correspondences."""

    rotation: Mat
    translation: Mat
    rms_m: float
    residuals_m: tuple[float, ...]

    def apply(self, points: Sequence[Sequence[float]] | Mat) -> Mat:
        pts = as_points(points)
        return np.asarray(pts @ self.rotation.T + self.translation, dtype=np.float64)

    def matrix(self) -> Mat:
        m = np.eye(4)
        m[:3, :3] = self.rotation
        m[:3, 3] = self.translation
        return m


def kabsch(
    source: Sequence[Sequence[float]] | Mat,
    target: Sequence[Sequence[float]] | Mat,
    *,
    yaw_only: bool = False,
) -> RigidTransform:
    """Least-squares rigid transform (no scale) mapping ``source`` points onto ``target``.

    With ``yaw_only`` the rotation is restricted to the vertical (+Y) axis, which needs only
    2 points and is the right model when both frames are known to be level.
    """
    src = as_points(source, minimum=2 if yaw_only else 3)
    dst = as_points(target, minimum=2 if yaw_only else 3)
    if src.shape != dst.shape:
        raise GeometryError(f"point sets differ in shape: {src.shape} vs {dst.shape}")
    cs, cd = src.mean(axis=0), dst.mean(axis=0)
    ps, pd = src - cs, dst - cd
    h = ps.T @ pd  # sum of p q^T
    if yaw_only:
        a = h[2, 0] - h[0, 2]
        b = h[0, 0] + h[2, 2]
        if math.hypot(a, b) < _EPS:
            raise GeometryError("points are degenerate in the horizontal plane")
        theta = math.atan2(a, b)
        c, s = math.cos(theta), math.sin(theta)
        rot = np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
    else:
        sv = np.linalg.svd(ps, compute_uv=False)
        if sv[1] < 1e-6 * max(sv[0], _EPS):
            raise GeometryError("points are collinear; the rotation about the line is undetermined")
        u, _, vt = np.linalg.svd(h)
        d = math.copysign(1.0, float(np.linalg.det(vt.T @ u.T)))
        rot = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    trans = cd - rot @ cs
    resid = np.linalg.norm(src @ rot.T + trans - dst, axis=1)
    return RigidTransform(
        rotation=rot,
        translation=trans,
        rms_m=float(np.sqrt(np.mean(resid**2))),
        residuals_m=tuple(float(r) for r in resid),
    )


# --- pivot (tip offset) calibration -----------------------------------------------------------


@dataclass(frozen=True)
class PivotResult:
    tip_offset: Vec3  # tip position in the controller's own frame (m)
    pivot_point: Vec3  # the fixed point in tracking space (m)
    rms_m: float
    n: int
    rotation_spread_deg: float
    condition: float


def rotation_spread_deg(rotations: Mat, *, max_samples: int = 300) -> float:
    """Largest angle between any two of the rotations (degrees)."""
    n = len(rotations)
    if n > max_samples:
        rotations = rotations[:: math.ceil(n / max_samples)]
    flat = rotations.reshape(len(rotations), 9)
    cos = np.clip((flat @ flat.T - 1.0) / 2.0, -1.0, 1.0)
    return float(np.degrees(np.arccos(cos.min())))


def pivot_calibration(
    rotations: Sequence[Sequence[Sequence[float]]] | Mat,
    positions: Sequence[Sequence[float]] | Mat,
    *,
    min_samples: int = 30,
    min_spread_deg: float = 25.0,
    max_condition: float = 60.0,
) -> PivotResult:
    """Solve for the controller-frame tip offset ``t`` and fixed world point ``q``.

    While the tip rests in a fixed divot and the controller is rotated about it, every pose
    ``(R_i, p_i)`` satisfies ``R_i t + p_i = q``. That is linear in ``(t, q)``.

    Rejects data that cannot determine ``t``: too few samples, too little rotation, or
    rotation about a single axis through the tip (rank deficient).
    """
    rots = np.asarray(rotations, dtype=np.float64)
    pos = as_points(positions, minimum=min_samples)
    if rots.shape != (len(pos), 3, 3):
        raise GeometryError(f"expected {len(pos)} rotation matrices, got shape {rots.shape}")
    spread = rotation_spread_deg(rots)
    if spread < min_spread_deg:
        raise GeometryError(
            f"controller rotated only {spread:.0f} deg during the pivot; need at least "
            f"{min_spread_deg:.0f} deg about different axes"
        )
    n = len(pos)
    a = np.zeros((3 * n, 6))
    b = np.zeros(3 * n)
    for i in range(n):
        a[3 * i : 3 * i + 3, :3] = rots[i]
        a[3 * i : 3 * i + 3, 3:] = -np.eye(3)
        b[3 * i : 3 * i + 3] = -pos[i]
    sv = np.linalg.svd(a, compute_uv=False)
    condition = float(sv[0] / max(sv[-1], _EPS))
    if condition > max_condition:
        raise GeometryError(
            f"pivot motion is poorly conditioned (condition {condition:.0f}); rotate the "
            "controller about more than one axis while keeping the tip still"
        )
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    resid = (a @ sol - b).reshape(n, 3)
    return PivotResult(
        tip_offset=to_vec3(sol[:3]),
        pivot_point=to_vec3(sol[3:]),
        rms_m=float(np.sqrt(np.mean(np.sum(resid**2, axis=1)))),
        n=n,
        rotation_spread_deg=spread,
        condition=condition,
    )
