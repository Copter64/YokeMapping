from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from yokemapping.overlay.app import OverlayApp
from yokemapping.overlay.ghost import (
    PoseFileError,
    YokePose,
    apply_offset,
    load_real_pose,
    overlay_matrix,
    render_ghost,
)
from yokemapping.overlay.panel import PANEL_H, PANEL_W, Panel, build_buttons
from yokemapping.overlay.state import (
    MAX_DEG,
    MAX_MM,
    Offset,
    OffsetFileError,
    OverlayState,
    load_offset,
    save_offset,
)


def write_pose(path: Path, rotation: list[list[float]] | None = None) -> Path:
    path.write_text(
        json.dumps(
            {
                "faceplate_centre_tracking_m": [0.5, 1.7, -0.2],
                "faceplate_rotation_tracking": rotation or np.eye(3).tolist(),
            }
        ),
        encoding="utf-8",
    )
    return path


# --- offset / state -----------------------------------------------------------------------------


def test_nudge_changes_one_axis_and_clamps() -> None:
    off = Offset().nudged("x", 5.0).nudged("x", -1.0)
    assert off.x_mm == 4.0 and off.y_mm == 0.0
    assert Offset().nudged("z", 10_000).z_mm == MAX_MM
    assert Offset().nudged("yaw", -999).yaw_deg == -MAX_DEG
    with pytest.raises(ValueError, match="unknown axis"):
        Offset().nudged("spin", 1)


def test_state_revision_changes_on_every_edit() -> None:
    state = OverlayState()
    seen = {state.revision}
    state.nudge("x", 1)
    seen.add(state.revision)
    state.toggle_ghost()
    seen.add(state.revision)
    state.say("hello")
    seen.add(state.revision)
    state.reset()
    seen.add(state.revision)
    assert len(seen) == 5 and state.offset.is_zero and not state.ghost_visible


def test_offset_file_round_trip_and_validation(tmp_path: Path) -> None:
    path = tmp_path / "off.json"
    save_offset(path, Offset(1.5, -2, 3, 0.5, -1, 2), {"extra": 1})
    assert load_offset(path) == Offset(1.5, -2, 3, 0.5, -1, 2)
    for bad in (
        "not json",
        json.dumps({"offset": {"x_mm": 1}}),
        json.dumps({"offset": {**Offset().__dict__, "x_mm": 9999}}),
        json.dumps({"offset": {**Offset().__dict__, "yaw_deg": "left"}}),
    ):
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(OffsetFileError):
            load_offset(path)
    with pytest.raises(OffsetFileError):
        load_offset(tmp_path / "missing.json")


# --- ghost --------------------------------------------------------------------------------------


def test_real_pose_is_converted_to_the_overlay_frame(tmp_path: Path) -> None:
    pose = load_real_pose(write_pose(tmp_path / "p.json"))
    assert pose.centre_m.tolist() == [0.5, 1.7, -0.2]
    # photo frame (y down, z into plate) -> overlay frame (y up, z toward the pilot)
    assert pose.rotation.tolist() == np.diag([1.0, -1.0, -1.0]).tolist()


@pytest.mark.parametrize(
    "rotation", [[[2, 0, 0], [0, 1, 0], [0, 0, 1]], [[1, 0, 0], [0, 1, 0], [0, 0, -1]]]
)
def test_bad_rotations_are_rejected(tmp_path: Path, rotation: list[list[float]]) -> None:
    with pytest.raises(PoseFileError):
        load_real_pose(write_pose(tmp_path / "p.json", rotation))


def test_malformed_pose_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(PoseFileError):
        load_real_pose(path)


def test_offset_moves_along_the_yokes_own_axes() -> None:
    yaw90 = np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], dtype=float)  # yoke x -> tracking -z
    pose = YokePose(np.array([1.0, 2.0, 3.0]), yaw90)
    moved = apply_offset(pose, Offset(x_mm=100, y_mm=20, z_mm=-10))
    expected = np.array([1.0, 2.0, 3.0]) + yaw90 @ np.array([0.1, 0.02, -0.01])
    assert moved.centre_m == pytest.approx(expected)
    assert moved.rotation == pytest.approx(yaw90)


def test_rotation_offsets_rotate_about_the_yoke_axes() -> None:
    pose = YokePose(np.zeros(3), np.eye(3))
    yawed = apply_offset(pose, Offset(yaw_deg=90))
    assert yawed.rotation @ np.array([0, 0, 1.0]) == pytest.approx([1, 0, 0], abs=1e-9)
    pitched = apply_offset(pose, Offset(pitch_deg=90))
    assert pitched.rotation @ np.array([0, 1.0, 0]) == pytest.approx([0, 0, 1], abs=1e-9)
    rolled = apply_offset(pose, Offset(roll_deg=90))
    assert rolled.rotation @ np.array([1.0, 0, 0]) == pytest.approx([0, 1, 0], abs=1e-9)
    assert np.linalg.det(
        apply_offset(pose, Offset(10, 20, 30, 15, -5, 7)).rotation
    ) == pytest.approx(1)


def test_overlay_matrix_is_row_major_3x4() -> None:
    pose = YokePose(np.array([1.0, 2.0, 3.0]), np.eye(3))
    assert overlay_matrix(pose) == [[1, 0, 0, 1.0], [0, 1, 0, 2.0], [0, 0, 1, 3.0]]


def test_ghost_texture_is_transparent_with_a_centre_cross() -> None:
    img = render_ghost()
    assert img.shape == (800, 800, 4) and img.dtype == np.uint8
    assert img[0, 0, 3] == 0  # corner is transparent
    assert img[400, 100, 3] > 0 and img[100, 400, 3] > 0  # the cross-hair arms


# --- panel and app ------------------------------------------------------------------------------


class FakeBackend:
    def __init__(self) -> None:
        self.panels = 0
        self.ghosts = 0
        self.matrix: list[list[float]] = []
        self.visible = True
        self.clicks: list[tuple[float, float]] = []
        self.closed = False

    def show_panel(self, rgba: np.ndarray) -> None:
        assert rgba.shape == (PANEL_H, PANEL_W, 4)
        self.panels += 1

    def show_ghost(self, rgba: np.ndarray) -> None:
        self.ghosts += 1

    def set_ghost_transform(self, matrix: list[list[float]]) -> None:
        self.matrix = matrix

    def set_ghost_visible(self, visible: bool) -> None:
        self.visible = visible

    def poll_clicks(self) -> list[tuple[float, float]]:
        out, self.clicks = self.clicks, []
        return out

    def close(self) -> None:
        self.closed = True


def centre_of(panel: Panel, ident: str) -> tuple[float, float]:
    button = next(b for b in panel.buttons if b.ident == ident)
    return button.x + button.w / 2, button.y + button.h / 2


def make_app(
    save_result: str = "saved", shot_error: Exception | None = None
) -> tuple[OverlayApp, FakeBackend, list[object]]:
    backend = FakeBackend()
    calls: list[object] = []

    def save(offset: Offset, virtual: YokePose) -> str:
        calls.append(("save", offset))
        return save_result

    def shot() -> str:
        if shot_error:
            raise shot_error
        calls.append("shot")
        return "shot saved"

    pose = YokePose(np.zeros(3), np.eye(3))
    return OverlayApp(backend, pose, OverlayState(), save, shot), backend, calls


def test_buttons_have_unique_ids_and_do_not_overlap() -> None:
    buttons = build_buttons()
    assert len({b.ident for b in buttons}) == len(buttons)
    for a in buttons:
        for b in buttons:
            if a is not b:
                assert not (
                    a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h
                )
        assert a.x >= 0 and a.x + a.w <= PANEL_W and a.y >= 0 and a.y + a.h <= PANEL_H


def test_hit_test_finds_the_button_and_misses_gaps() -> None:
    panel = Panel()
    for button in panel.buttons:
        assert panel.hit_test(*centre_of(panel, button.ident)) == button.ident
    assert panel.hit_test(1, 1) is None


def test_panel_render_reflects_the_state() -> None:
    panel, state = Panel(), OverlayState()
    before = panel.render(state)
    state.nudge("x", 5)
    assert not np.array_equal(before, panel.render(state))


def test_clicking_a_nudge_button_moves_the_ghost() -> None:
    app, backend, _ = make_app()
    backend.clicks.append(centre_of(app.panel, "nudge:x:5"))
    app.step()
    assert app.state.offset.x_mm == 5.0
    assert backend.matrix[0][3] == pytest.approx(0.005)  # ghost moved 5 mm along yoke x


def test_toggle_reset_save_and_screenshot_buttons() -> None:
    app, backend, calls = make_app()
    for ident in ("nudge:yaw:2", "toggle_ghost", "save", "screenshot", "reset"):
        backend.clicks.append(centre_of(app.panel, ident))
    app.step()
    assert calls[0] == ("save", Offset(yaw_deg=2.0)) and calls[1] == "shot"
    assert app.state.offset.is_zero and not backend.visible
    assert app.state.message == "Offset reset"


def test_failing_action_is_reported_not_raised() -> None:
    app, backend, _ = make_app(shot_error=RuntimeError("no game"))
    backend.clicks.append(centre_of(app.panel, "screenshot"))
    app.step()
    assert "no game" in app.state.message


def test_step_only_redraws_when_something_changed() -> None:
    app, backend, _ = make_app()
    drawn = backend.panels
    app.step()
    app.step()
    assert backend.panels == drawn
    backend.clicks.append(centre_of(app.panel, "nudge:y:1"))
    app.step()
    assert backend.panels == drawn + 1
