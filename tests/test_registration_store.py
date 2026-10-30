from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from yokemapping.registration.capture import CapturedPoint
from yokemapping.registration.geometry import kabsch
from yokemapping.registration.store import (
    CaptureFileError,
    LandmarkFile,
    TipFile,
    file_kind,
    load_alignment,
    load_landmarks,
    load_tip,
    save_alignment,
    save_landmarks,
    save_tip,
)
from yokemapping.registration.yoke import Alignment

TIP = TipFile("CTRL-1", (0.02, -0.03, 0.11), 0.0006, 900, 140.0, "2026-10-29T20:00:00+00:00")
LANDMARKS = LandmarkFile(
    "real",
    "CTRL-1",
    (0.02, -0.03, 0.11),
    "2026-10-29T20:05:00+00:00",
    {
        "shaft_1": CapturedPoint((0.0, 0.85, -0.7), 90, 0.001),
        "left_grip": CapturedPoint((-0.17, 0.87, -0.4), 88, 0.0015),
    },
)


def test_tip_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "tip.json"
    save_tip(path, TIP)  # creates the folder
    assert load_tip(path) == TIP
    assert file_kind(path) == "tip"


def test_landmarks_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "real.json"
    save_landmarks(path, LANDMARKS)
    assert load_landmarks(path) == LANDMARKS
    assert file_kind(path) == "landmarks"


def test_alignment_round_trip(tmp_path: Path) -> None:
    src = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    tf = kabsch(src, src + np.array([0.1, 0.0, -0.2]))
    names = ("shaft_1", "shaft_2", "left_grip", "right_grip")
    path = tmp_path / "alignment.json"
    save_alignment(
        path,
        real_file="r.json",
        virtual_file="v.json",
        alignment=Alignment(tf, names, False),
        created="2026-10-29T20:10:00+00:00",
    )
    loaded = load_alignment(path)
    assert loaded.names == names
    assert loaded.translation == pytest.approx((0.1, 0.0, -0.2), abs=1e-9)
    assert loaded.rotation == pytest.approx(np.eye(3), abs=1e-9)
    assert loaded.rms_m == pytest.approx(0.0, abs=1e-9)
    assert (loaded.real_file, loaded.virtual_file, loaded.yaw_only) == ("r.json", "v.json", False)
    assert file_kind(path) == "alignment"


def write(path: Path, payload: Any) -> Path:
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def good_landmarks() -> dict[str, Any]:
    return {
        "schema": 1,
        "kind": "landmarks",
        "created": "t",
        "target": "real",
        "serial": "S",
        "tip_offset_m": [0, 0, 0.1],
        "landmarks": {"shaft_1": {"position_m": [0, 1, 0], "n": 5, "spread_m": 0.001}},
    }


def test_load_rejects_missing_and_malformed_files(tmp_path: Path) -> None:
    with pytest.raises(CaptureFileError, match="not found"):
        load_tip(tmp_path / "nope.json")
    with pytest.raises(CaptureFileError, match="not valid JSON"):
        load_tip(write(tmp_path / "a.json", "{not json"))
    with pytest.raises(CaptureFileError, match="JSON object"):
        load_tip(write(tmp_path / "b.json", "[1, 2]"))
    with pytest.raises(CaptureFileError, match="missing 'kind'"):
        file_kind(write(tmp_path / "c.json", {"schema": 1}))


def test_load_rejects_wrong_schema_and_kind(tmp_path: Path) -> None:
    data = good_landmarks()
    with pytest.raises(CaptureFileError, match="expected a 'tip' file"):
        load_tip(write(tmp_path / "a.json", data))
    data["schema"] = 2
    with pytest.raises(CaptureFileError, match="unsupported schema"):
        load_landmarks(write(tmp_path / "b.json", data))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("target", "both", "target must be"),
        ("serial", 5, "'serial' must be a string"),
        ("tip_offset_m", [0, 0], "list of 3 numbers"),
        ("tip_offset_m", [0, "x", 0], "list of 3 numbers"),
        ("landmarks", {}, "non-empty object"),
        ("landmarks", {"a": 5}, "must be an object"),
        (
            "landmarks",
            {"a": {"position_m": [0, 0, 0], "n": "x", "spread_m": 0}},
            "must be a number",
        ),
        ("landmarks", {"a": {"position_m": [0, 0], "n": 1, "spread_m": 0}}, "position_m"),
    ],
)
def test_landmark_file_field_validation(
    tmp_path: Path, field: str, value: Any, message: str
) -> None:
    data = good_landmarks()
    data[field] = value
    with pytest.raises(CaptureFileError, match=message):
        load_landmarks(write(tmp_path / "x.json", data))


def test_alignment_file_validation(tmp_path: Path) -> None:
    base: dict[str, Any] = {
        "schema": 1,
        "kind": "alignment",
        "created": "t",
        "real_file": "r",
        "virtual_file": "v",
        "yaw_only": False,
        "landmarks": ["a"],
        "rotation": np.eye(3).tolist(),
        "translation_m": [0, 0, 0],
        "rms_m": 0.0,
        "residuals_mm": {"a": 0.0},
    }
    assert load_alignment(write(tmp_path / "ok.json", base)).names == ("a",)
    bad_rotation = {**base, "rotation": (2 * np.eye(3)).tolist()}
    with pytest.raises(CaptureFileError, match="proper rotation"):
        load_alignment(write(tmp_path / "r.json", bad_rotation))
    with pytest.raises(CaptureFileError, match="3x3"):
        load_alignment(write(tmp_path / "s.json", {**base, "rotation": [[1, 0, 0]]}))
    with pytest.raises(CaptureFileError, match="list of names"):
        load_alignment(write(tmp_path / "n.json", {**base, "landmarks": "a"}))
    with pytest.raises(CaptureFileError, match="residuals_mm"):
        load_alignment(write(tmp_path / "m.json", {**base, "residuals_mm": []}))
