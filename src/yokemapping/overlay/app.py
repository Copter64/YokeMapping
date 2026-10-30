"""Overlay application logic, independent of OpenVR (the backend is injected).

Each ``step`` handles pending clicks, redraws the panel when the state changed and keeps the
ghost's transform and visibility current.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from yokemapping.overlay.ghost import YokePose, apply_offset, overlay_matrix, render_ghost
from yokemapping.overlay.panel import Panel
from yokemapping.overlay.state import Offset, OverlayState

log = logging.getLogger(__name__)


class OverlayBackend(Protocol):
    def show_panel(self, rgba: NDArray[np.uint8]) -> None: ...
    def show_ghost(self, rgba: NDArray[np.uint8]) -> None: ...
    def set_ghost_transform(self, matrix: list[list[float]]) -> None: ...
    def set_ghost_visible(self, visible: bool) -> None: ...
    def poll_clicks(self) -> list[tuple[float, float]]: ...
    def close(self) -> None: ...


SaveAction = Callable[[Offset, YokePose], str]
ScreenshotAction = Callable[[], str]


class OverlayApp:
    def __init__(
        self,
        backend: OverlayBackend,
        real_pose: YokePose,
        state: OverlayState,
        save: SaveAction,
        screenshot: ScreenshotAction,
    ) -> None:
        self.backend = backend
        self.real_pose = real_pose
        self.state = state
        self.panel = Panel()
        self._save = save
        self._screenshot = screenshot
        self._drawn_revision = -1
        backend.show_ghost(render_ghost())
        self._redraw()

    def virtual_pose(self) -> YokePose:
        return apply_offset(self.real_pose, self.state.offset)

    def _redraw(self) -> None:
        self.backend.show_panel(self.panel.render(self.state))
        self.backend.set_ghost_transform(overlay_matrix(self.virtual_pose()))
        self.backend.set_ghost_visible(self.state.ghost_visible)
        self._drawn_revision = self.state.revision

    def handle(self, ident: str) -> None:
        if ident.startswith("nudge:"):
            _, axis, amount = ident.split(":")
            self.state.nudge(axis, float(amount))
        elif ident == "toggle_ghost":
            self.state.toggle_ghost()
        elif ident == "reset":
            self.state.reset()
        elif ident == "save":
            self._run(lambda: self._save(self.state.offset, self.virtual_pose()))
        elif ident == "screenshot":
            self.state.say("Taking a screenshot in 3 s - hold still")
            self._redraw()
            self._run(self._screenshot)
        else:
            log.warning("unknown panel action %r", ident)

    def _run(self, action: Callable[[], str]) -> None:
        try:
            self.state.say(action())
        except Exception as exc:  # shown in the headset instead of crashing the overlay
            log.exception("panel action failed")
            self.state.say(f"Failed: {exc}")

    def step(self) -> None:
        for px, py in self.backend.poll_clicks():
            ident = self.panel.hit_test(px, py)
            if ident is not None:
                self.handle(ident)
        if self.state.revision != self._drawn_revision:
            self._redraw()
