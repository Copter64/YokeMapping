"""The dashboard panel: button layout, drawing and hit-testing (no OpenVR here).

Coordinates are pixels with the origin at the top left.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw, ImageFont

from yokemapping.overlay.state import AXES, OverlayState

PANEL_W, PANEL_H = 1000, 720
MM_STEPS = (-5.0, -1.0, 1.0, 5.0)
DEG_STEPS = (-2.0, -0.5, 0.5, 2.0)
AXIS_LABELS = {
    "x": "Left / Right",
    "y": "Down / Up",
    "z": "Away / Closer",
    "yaw": "Yaw",
    "pitch": "Pitch",
    "roll": "Roll",
}
BACKGROUND = (20, 24, 32, 255)
BUTTON = (55, 66, 92, 255)
BUTTON_HOT = (30, 120, 90, 255)
TEXT = (235, 240, 250, 255)


@dataclass(frozen=True)
class Button:
    ident: str
    label: str
    x: int
    y: int
    w: int
    h: int

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


def _step_label(step: float) -> str:
    return f"{step:+g}"


def build_buttons() -> list[Button]:
    buttons: list[Button] = []
    for row, axis in enumerate(AXES):
        steps = MM_STEPS if axis in ("x", "y", "z") else DEG_STEPS
        y = 100 + row * 70
        for col, step in enumerate(steps):
            buttons.append(
                Button(f"nudge:{axis}:{step:g}", _step_label(step), 270 + col * 130, y, 120, 56)
            )
    for col, (ident, label) in enumerate(
        [
            ("toggle_ghost", "Ghost"),
            ("reset", "Reset"),
            ("save", "Save"),
            ("screenshot", "Screenshot"),
        ]
    ):
        buttons.append(Button(ident, label, 20 + col * 245, 540, 225, 70))
    return buttons


class Panel:
    def __init__(self) -> None:
        self.buttons = build_buttons()
        self._font = ImageFont.load_default(size=28)
        self._small = ImageFont.load_default(size=22)

    def hit_test(self, px: float, py: float) -> str | None:
        for button in self.buttons:
            if button.contains(px, py):
                return button.ident
        return None

    def render(self, state: OverlayState) -> NDArray[np.uint8]:
        img = Image.new("RGBA", (PANEL_W, PANEL_H), BACKGROUND)
        draw = ImageDraw.Draw(img)
        draw.text(
            (20, 20),
            "YokeMapping - nudge the ghost onto the virtual yoke",
            font=self._font,
            fill=TEXT,
        )
        draw.text(
            (20, 60),
            "Ghost = real yoke. Line it up with the virtual yoke's centre.",
            font=self._small,
            fill=TEXT,
        )
        for row, axis in enumerate(AXES):
            y = 100 + row * 70
            draw.text((20, y + 12), AXIS_LABELS[axis], font=self._font, fill=TEXT)
            unit = "mm" if axis in ("x", "y", "z") else "deg"
            draw.text(
                (810, y + 12), f"{state.offset.value(axis):+.1f} {unit}", font=self._font, fill=TEXT
            )
        for button in self.buttons:
            hot = button.ident == "toggle_ghost" and state.ghost_visible
            draw.rounded_rectangle(
                [button.x, button.y, button.x + button.w, button.y + button.h],
                radius=10,
                fill=BUTTON_HOT if hot else BUTTON,
            )
            label = button.label
            if button.ident == "toggle_ghost":
                label = "Ghost: ON" if state.ghost_visible else "Ghost: OFF"
            box = draw.textbbox((0, 0), label, font=self._font)
            draw.text(
                (
                    button.x + (button.w - (box[2] - box[0])) / 2,
                    button.y + (button.h - (box[3] - box[1])) / 2 - 4,
                ),
                label,
                font=self._font,
                fill=TEXT,
            )
        draw.text((20, 640), state.message[:80], font=self._small, fill=TEXT)
        return np.asarray(img, dtype=np.uint8)
