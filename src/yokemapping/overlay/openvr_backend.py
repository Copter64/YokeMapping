"""OpenVR implementation of the overlay backend (dashboard panel + world-space ghost)."""

from __future__ import annotations

import ctypes
import logging

import numpy as np
import openvr
from numpy.typing import NDArray

from yokemapping.overlay.panel import PANEL_H, PANEL_W

log = logging.getLogger(__name__)
PANEL_KEY = "yokemapping.panel"
GHOST_KEY = "yokemapping.ghost"
PANEL_WIDTH_M = 1.6
GHOST_ALPHA = 0.85


class OpenVROverlayBackend:
    """Needs ``openvr.init(VRApplication_Overlay)`` to have been called already."""

    def __init__(self, ghost_width_m: float, flip_y: bool = True) -> None:
        self._ov = openvr.VROverlay()
        self._flip_y = flip_y
        self._panel, self._thumb = self._ov.createDashboardOverlay(PANEL_KEY, "YokeMapping")
        self._ov.setOverlayWidthInMeters(self._panel, PANEL_WIDTH_M)
        self._ov.setOverlayInputMethod(self._panel, openvr.VROverlayInputMethod_Mouse)
        scale = openvr.HmdVector2_t()
        scale.v[0], scale.v[1] = float(PANEL_W), float(PANEL_H)
        self._ov.setOverlayMouseScale(self._panel, scale)
        self._ghost = self._ov.createOverlay(GHOST_KEY, "YokeMapping ghost")
        self._ov.setOverlayWidthInMeters(self._ghost, ghost_width_m)
        self._ov.setOverlayAlpha(self._ghost, GHOST_ALPHA)
        self._event = openvr.VREvent_t()
        self._keep: list[object] = []  # raw buffers must outlive the upload call
        self._set_thumbnail()

    def _upload(self, handle: int, rgba: NDArray[np.uint8]) -> None:
        height, width = rgba.shape[:2]
        buf = (ctypes.c_ubyte * rgba.nbytes).from_buffer_copy(rgba.tobytes())
        self._keep = [buf]
        self._ov.setOverlayRaw(handle, buf, width, height, 4)

    def _set_thumbnail(self) -> None:
        icon = np.zeros((128, 128, 4), dtype=np.uint8)
        icon[:, :] = (20, 24, 32, 255)
        icon[62:66, 16:112] = (0, 230, 255, 255)
        icon[16:112, 62:66] = (0, 230, 255, 255)
        self._upload(self._thumb, icon)

    def show_panel(self, rgba: NDArray[np.uint8]) -> None:
        self._upload(self._panel, rgba)

    def show_ghost(self, rgba: NDArray[np.uint8]) -> None:
        self._upload(self._ghost, rgba)
        self._ov.showOverlay(self._ghost)

    def set_ghost_transform(self, matrix: list[list[float]]) -> None:
        m = openvr.HmdMatrix34_t()
        for r in range(3):
            for c in range(4):
                m.m[r][c] = matrix[r][c]
        self._ov.setOverlayTransformAbsolute(self._ghost, openvr.TrackingUniverseStanding, m)

    def set_ghost_visible(self, visible: bool) -> None:
        if visible:
            self._ov.showOverlay(self._ghost)
        else:
            self._ov.hideOverlay(self._ghost)

    def poll_clicks(self) -> list[tuple[float, float]]:
        clicks: list[tuple[float, float]] = []
        while True:
            more, _ = self._ov.pollNextOverlayEvent(self._panel, self._event)
            if not more:
                break
            if (
                self._event.eventType == openvr.VREvent_MouseButtonDown
                and self._event.data.mouse.button == openvr.VRMouseButton_Left
            ):
                x, y = float(self._event.data.mouse.x), float(self._event.data.mouse.y)
                clicks.append((x, PANEL_H - y if self._flip_y else y))
        return clicks

    def close(self) -> None:
        for handle in (self._ghost, self._panel):
            try:
                self._ov.destroyOverlay(handle)
            except openvr.error_code.OverlayError:
                log.debug("overlay %s already gone", handle)
