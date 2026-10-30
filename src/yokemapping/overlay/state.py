"""Overlay state: the nudge applied to the ghost, and its persistence.

The offset is expressed in the yoke's own frame (x right, y up, z toward the pilot), so "left",
"up" and "closer" mean what they look like from the seat, whatever the tracking-space axes are.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AXES = ("x", "y", "z", "yaw", "pitch", "roll")
_FIELD = {
    "x": "x_mm",
    "y": "y_mm",
    "z": "z_mm",
    "yaw": "yaw_deg",
    "pitch": "pitch_deg",
    "roll": "roll_deg",
}
MAX_MM = 500.0
MAX_DEG = 45.0


class OffsetFileError(ValueError):
    """A saved offset file is missing fields or has non-numeric / out-of-range values."""


@dataclass(frozen=True)
class Offset:
    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0

    def nudged(self, axis: str, amount: float) -> Offset:
        """A copy with ``axis`` changed by ``amount`` (mm or degrees), clamped to sane limits."""
        if axis not in _FIELD:
            raise ValueError(f"unknown axis {axis!r}; expected one of {', '.join(AXES)}")
        name = _FIELD[axis]
        limit = MAX_MM if name.endswith("_mm") else MAX_DEG
        value = max(-limit, min(limit, getattr(self, name) + amount))
        return replace(self, **{name: round(value, 6)})

    def value(self, axis: str) -> float:
        return float(getattr(self, _FIELD[axis]))

    @property
    def is_zero(self) -> bool:
        return all(v == 0.0 for v in asdict(self).values())


class OverlayState:
    """Mutable UI state: the current offset, ghost visibility and a status line."""

    def __init__(self, offset: Offset | None = None) -> None:
        self.offset = offset or Offset()
        self.ghost_visible = True
        self.message = "Ready"
        self.revision = 0  # bumped on every change so the app knows when to redraw

    def _changed(self) -> None:
        self.revision += 1

    def nudge(self, axis: str, amount: float) -> None:
        self.offset = self.offset.nudged(axis, amount)
        self._changed()

    def reset(self) -> None:
        self.offset = Offset()
        self.message = "Offset reset"
        self._changed()

    def toggle_ghost(self) -> None:
        self.ghost_visible = not self.ghost_visible
        self._changed()

    def say(self, text: str) -> None:
        self.message = text
        self._changed()


def save_offset(path: Path, offset: Offset, extra: dict[str, Any] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "kind": "virtual_offset",
        "created": datetime.now(UTC).isoformat(),
        "frame": "yoke (x right, y up, z toward the pilot); virtual = real * offset",
        "offset": asdict(offset),
    }
    if extra:
        data.update(extra)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_offset(path: Path) -> Offset:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data["offset"]
        values = {key: float(raw[key]) for key in asdict(Offset())}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OffsetFileError(f"{path}: not a valid offset file ({exc})") from exc
    for key, value in values.items():
        limit = MAX_MM if key.endswith("_mm") else MAX_DEG
        if not math.isfinite(value) or abs(value) > limit:
            raise OffsetFileError(f"{path}: {key}={value} is outside +/-{limit}")
    return Offset(**values)
