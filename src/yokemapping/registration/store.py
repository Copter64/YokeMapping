"""JSON files for tip calibrations, landmark captures and alignments.

Every file carries ``schema`` (currently 1) and ``kind``. Loading validates both and
every field, so a hand-edited or truncated file fails with a clear message instead of
producing a silently wrong registration. Positions are meters in SteamVR tracking space.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from yokemapping.registration.capture import CapturedPoint
from yokemapping.registration.geometry import Mat, Vec3
from yokemapping.registration.yoke import Alignment

SCHEMA_VERSION = 1


class CaptureFileError(ValueError):
    """Raised when a capture file is missing, malformed, or of the wrong kind."""


@dataclass(frozen=True)
class TipFile:
    serial: str
    tip_offset_m: Vec3
    rms_m: float
    n: int
    rotation_spread_deg: float
    created: str


@dataclass(frozen=True)
class LandmarkFile:
    target: str  # "real" or "virtual"
    serial: str
    tip_offset_m: Vec3
    created: str
    landmarks: dict[str, CapturedPoint]


@dataclass(frozen=True, eq=False)
class AlignmentFile:
    """``virtual = rotation @ real + translation``."""

    real_file: str
    virtual_file: str
    yaw_only: bool
    names: tuple[str, ...]
    rotation: Mat
    translation: Vec3
    rms_m: float
    residuals_mm: dict[str, float]
    created: str


# --- helpers ----------------------------------------------------------------------------------


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _read(path: Path, kind: str) -> dict[str, Any]:
    data = _load_json(path)
    if data.get("schema") != SCHEMA_VERSION:
        raise CaptureFileError(f"{path}: unsupported schema {data.get('schema')!r}")
    if data.get("kind") != kind:
        raise CaptureFileError(f"{path}: expected a '{kind}' file, found {data.get('kind')!r}")
    return data


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CaptureFileError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CaptureFileError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CaptureFileError(f"{path}: expected a JSON object")
    return data


def file_kind(path: Path) -> str:
    """The ``kind`` of a capture file ('tip', 'landmarks' or 'alignment')."""
    kind = _load_json(path).get("kind")
    if not isinstance(kind, str):
        raise CaptureFileError(f"{path}: missing 'kind'")
    return kind


def _num(data: dict[str, Any], key: str, where: str = "") -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CaptureFileError(f"{where}{key!r} must be a number, got {value!r}")
    return float(value)


def _text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise CaptureFileError(f"{key!r} must be a string, got {value!r}")
    return value


def _vec3(value: Any, what: str) -> Vec3:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in value)
    ):
        raise CaptureFileError(f"{what} must be a list of 3 numbers, got {value!r}")
    return (float(value[0]), float(value[1]), float(value[2]))


# --- tip --------------------------------------------------------------------------------------


def save_tip(path: Path, tip: TipFile) -> None:
    _write(
        path,
        {
            "schema": SCHEMA_VERSION,
            "kind": "tip",
            "created": tip.created,
            "serial": tip.serial,
            "tip_offset_m": list(tip.tip_offset_m),
            "rms_m": tip.rms_m,
            "n": tip.n,
            "rotation_spread_deg": tip.rotation_spread_deg,
        },
    )


def load_tip(path: Path) -> TipFile:
    d = _read(path, "tip")
    return TipFile(
        serial=_text(d, "serial"),
        tip_offset_m=_vec3(d.get("tip_offset_m"), "tip_offset_m"),
        rms_m=_num(d, "rms_m"),
        n=int(_num(d, "n")),
        rotation_spread_deg=_num(d, "rotation_spread_deg"),
        created=_text(d, "created"),
    )


# --- landmarks --------------------------------------------------------------------------------


def save_landmarks(path: Path, data: LandmarkFile) -> None:
    _write(
        path,
        {
            "schema": SCHEMA_VERSION,
            "kind": "landmarks",
            "created": data.created,
            "target": data.target,
            "serial": data.serial,
            "tip_offset_m": list(data.tip_offset_m),
            "landmarks": {
                name: {"position_m": list(p.position), "n": p.n, "spread_m": p.spread_m}
                for name, p in data.landmarks.items()
            },
        },
    )


def load_landmarks(path: Path) -> LandmarkFile:
    d = _read(path, "landmarks")
    target = _text(d, "target")
    if target not in ("real", "virtual"):
        raise CaptureFileError(f"{path}: target must be 'real' or 'virtual', got {target!r}")
    raw = d.get("landmarks")
    if not isinstance(raw, dict) or not raw:
        raise CaptureFileError(f"{path}: 'landmarks' must be a non-empty object")
    landmarks: dict[str, CapturedPoint] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise CaptureFileError(f"{path}: landmark {name!r} must be an object")
        landmarks[name] = CapturedPoint(
            position=_vec3(entry.get("position_m"), f"{name}.position_m"),
            n=int(_num(entry, "n", f"{name}.")),
            spread_m=_num(entry, "spread_m", f"{name}."),
        )
    return LandmarkFile(
        target=target,
        serial=_text(d, "serial"),
        tip_offset_m=_vec3(d.get("tip_offset_m"), "tip_offset_m"),
        created=_text(d, "created"),
        landmarks=landmarks,
    )


# --- alignment --------------------------------------------------------------------------------


def save_alignment(
    path: Path, *, real_file: str, virtual_file: str, alignment: Alignment, created: str
) -> None:
    tf = alignment.transform
    _write(
        path,
        {
            "schema": SCHEMA_VERSION,
            "kind": "alignment",
            "created": created,
            "real_file": real_file,
            "virtual_file": virtual_file,
            "yaw_only": alignment.yaw_only,
            "landmarks": list(alignment.names),
            "rotation": tf.rotation.tolist(),
            "translation_m": [float(v) for v in tf.translation],
            "rms_m": tf.rms_m,
            "residuals_mm": alignment.residuals_mm(),
        },
    )


def load_alignment(path: Path) -> AlignmentFile:
    d = _read(path, "alignment")
    rot_raw = d.get("rotation")
    if not isinstance(rot_raw, list) or len(rot_raw) != 3:
        raise CaptureFileError(f"{path}: 'rotation' must be a 3x3 matrix")
    rot = np.array([_vec3(row, "rotation row") for row in rot_raw], dtype=np.float64)
    if abs(float(np.linalg.det(rot)) - 1.0) > 1e-3 or not np.allclose(
        rot @ rot.T, np.eye(3), atol=1e-3
    ):
        raise CaptureFileError(f"{path}: 'rotation' is not a proper rotation matrix")
    names = d.get("landmarks")
    resid = d.get("residuals_mm")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise CaptureFileError(f"{path}: 'landmarks' must be a list of names")
    if not isinstance(resid, dict):
        raise CaptureFileError(f"{path}: 'residuals_mm' must be an object")
    return AlignmentFile(
        real_file=_text(d, "real_file"),
        virtual_file=_text(d, "virtual_file"),
        yaw_only=bool(d.get("yaw_only")),
        names=tuple(names),
        rotation=rot,
        translation=_vec3(d.get("translation_m"), "translation_m"),
        rms_m=_num(d, "rms_m"),
        residuals_mm={str(k): float(v) for k, v in resid.items()},
        created=_text(d, "created"),
    )
