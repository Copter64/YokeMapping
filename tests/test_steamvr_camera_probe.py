from __future__ import annotations

import ctypes
import json
from pathlib import Path
from typing import Any

import openvr
import pytest

from yokemapping.probes import steamvr_camera as probe
from yokemapping.probes.steamvr_camera import (
    DeviceReport,
    ProbeReport,
    StreamResult,
    Verdict,
    format_report,
    mean_brightness,
    verdict,
    write_ppm,
)

NO_FRAME = openvr.error_code.TrackedCameraError_NoFrameAvailable


def _err(cls: type[Exception]) -> Exception:
    return cls(0)  # pyopenvr's ErrorCode requires an error_value argument


class FakeHeader:
    def __init__(self, seq: int, width: int = 4, height: int = 2) -> None:
        self.nFrameSequence = seq
        self.nWidth = width
        self.nHeight = height
        self.nBytesPerPixel = 4


class FakeCamera:
    def __init__(
        self,
        frames: list[FakeHeader | Exception] | None = None,
        *,
        has_camera: bool = True,
        no_camera_indices: frozenset[int] = frozenset(),
        fill: int = 128,
        acquire_error: Exception | None = None,
    ) -> None:
        self.frames = list(frames or [])
        self.has_camera = has_camera
        self.no_camera_indices = no_camera_indices
        self.fill = fill
        self.acquire_error = acquire_error
        self.released = False

    def hasCamera(self, index: int) -> ctypes.c_bool:
        return ctypes.c_bool(self.has_camera and index not in self.no_camera_indices)

    def getCameraFrameSize(self, index: int, frame_type: int) -> tuple[int, int, int]:
        return 4, 2, 4 * 2 * 4

    def acquireVideoStreamingService(self, index: int) -> int:
        if self.acquire_error:
            raise self.acquire_error
        return 7

    def getVideoStreamFrameBuffer(
        self, handle: int, frame_type: int, buffer: Any, size: int
    ) -> FakeHeader:
        if not self.frames:
            raise _err(NO_FRAME)
        item = self.frames.pop(0)
        if isinstance(item, Exception):
            raise item
        ctypes.memset(buffer, self.fill, size)
        return item

    def releaseVideoStreamingService(self, handle: int) -> None:
        self.released = True


class FakeSystem:
    def __init__(self, classes: dict[int, int]) -> None:
        self.classes = classes

    def getTrackedDeviceClass(self, index: int) -> int:
        return int(self.classes.get(index, openvr.TrackedDeviceClass_Invalid))

    def getStringTrackedDeviceProperty(self, index: int, prop: int) -> str:
        if prop == openvr.Prop_SerialNumber_String:
            raise _err(openvr.error_code.TrackedPropertyError)
        return "Steam Frame"

    def getInt32TrackedDeviceProperty(self, index: int, prop: int) -> int:
        if prop == openvr.Prop_NumCameras_Int32:
            return 4
        return int(openvr.EVRTrackedCameraFrameLayout_Stereo)


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe, "POLL_INTERVAL_S", 0.0)


def _hmd(stream: StreamResult | None = None, *, has_camera: bool = True) -> DeviceReport:
    return DeviceReport(index=0, device_class="HMD", has_camera=has_camera, stream=stream)


def _stream(distinct: int, brightness: float | None, error: str | None = None) -> StreamResult:
    return StreamResult(
        frame_type="distorted",
        duration_s=1.0,
        frames_read=distinct,
        distinct_frames=distinct,
        width=640,
        height=480,
        mean_brightness=brightness,
        error=error,
    )


# --- pure helpers ---------------------------------------------------------------------------


def test_mean_brightness_ignores_alpha() -> None:
    assert mean_brightness(bytes([255, 255, 255, 0]) * 10) == 255
    assert mean_brightness(bytes([0, 0, 0, 255]) * 10) == 0
    assert mean_brightness(bytes([30, 60, 90, 0])) == 60


def test_mean_brightness_empty_and_sampled() -> None:
    assert mean_brightness(b"") == 0.0
    big = bytes([100, 100, 100, 0]) * 500_000
    assert mean_brightness(big, max_samples=1_000) == 100


def test_write_ppm(tmp_path: Path) -> None:
    out = tmp_path / "f.ppm"
    write_ppm(out, bytes([1, 2, 3, 99, 4, 5, 6, 99]), 2, 1)
    assert out.read_bytes() == b"P6\n2 1\n255\n" + bytes([1, 2, 3, 4, 5, 6])


def test_write_ppm_rejects_short_buffer(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="expected at least"):
        write_ppm(tmp_path / "f.ppm", b"\x00" * 4, 2, 1)


# --- verdict ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("report", "expected", "fragment"),
    [
        (ProbeReport(False, False), Verdict.INCONCLUSIVE, "not installed"),
        (
            ProbeReport(True, True, init_error="InitError_Init_NoServerForBackgroundApp"),
            Verdict.INCONCLUSIVE,
            "Start SteamVR",
        ),
        (
            ProbeReport(True, True, devices=[DeviceReport(1, "Controller")]),
            Verdict.INCONCLUSIVE,
            "no headset",
        ),
        (
            ProbeReport(True, True, devices=[_hmd(has_camera=False)]),
            Verdict.UNAVAILABLE,
            "No tracked device reports a camera",
        ),
        (
            ProbeReport(True, True, devices=[_hmd(_stream(30, 80.0))]),
            Verdict.AVAILABLE,
            "640x480 (distorted) at ~30 fps",
        ),
        (
            ProbeReport(True, True, devices=[_hmd(_stream(30, 0.0))]),
            Verdict.INCONCLUSIVE,
            "black",
        ),
        (
            ProbeReport(
                True, True, devices=[_hmd(_stream(0, None, error="getVideoStreamFrameBuffer: X"))]
            ),
            Verdict.UNAVAILABLE,
            "getVideoStreamFrameBuffer: X",
        ),
        (
            ProbeReport(True, True, devices=[_hmd(_stream(1, 80.0))]),
            Verdict.UNAVAILABLE,
            "no frames could be streamed",
        ),
    ],
)
def test_verdict(report: ProbeReport, expected: Verdict, fragment: str) -> None:
    result, explanation = verdict(report)
    assert result is expected
    assert fragment in explanation


def test_format_report_includes_devices_and_verdict() -> None:
    report = ProbeReport(True, True, devices=[_hmd(_stream(30, 80.0))])
    text = format_report(report)
    assert "[0] HMD" in text
    assert "30 distinct" in text
    assert text.splitlines()[-1].startswith("VERDICT: AVAILABLE")


# --- OpenVR-driving code, against fakes ---------------------------------------------------------


def test_stream_counts_distinct_frames_and_saves(tmp_path: Path) -> None:
    camera = FakeCamera(
        [FakeHeader(1), FakeHeader(1), FakeHeader(2), _err(NO_FRAME), FakeHeader(3)]
    )
    result = probe._stream(camera, 0, "distorted", 0.05, tmp_path / "frame.ppm")
    assert (result.frames_read, result.distinct_frames) == (4, 3)
    assert (result.width, result.height, result.bytes_per_pixel) == (4, 2, 4)
    assert result.mean_brightness == 128
    assert result.error is None
    assert result.saved_frame is not None
    assert Path(result.saved_frame).name == "frame_dev0.ppm"
    assert Path(result.saved_frame).read_bytes().startswith(b"P6\n4 2\n255\n")
    assert camera.released


def test_stream_reports_acquire_failure() -> None:
    camera = FakeCamera(acquire_error=_err(openvr.error_code.TrackedCameraError_StreamSetupFailure))
    result = probe._stream(camera, 0, "distorted", 0.01, None)
    assert result.error == "acquireVideoStreamingService: TrackedCameraError_StreamSetupFailure"
    assert not camera.released


def test_stream_stops_on_hard_error_and_releases() -> None:
    camera = FakeCamera([FakeHeader(1), _err(openvr.error_code.TrackedCameraError_IPCFailure)])
    result = probe._stream(camera, 0, "distorted", 1.0, None)
    assert result.frames_read == 1
    assert result.error == "getVideoStreamFrameBuffer: TrackedCameraError_IPCFailure"
    assert camera.released


def test_run_probe_runtime_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openvr, "isRuntimeInstalled", lambda: False)
    assert probe.run_probe() == ProbeReport(runtime_installed=False, hmd_present=False)


def test_run_probe_init_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(app_type: int) -> None:
        raise _err(openvr.error_code.InitError_Init_NoServerForBackgroundApp)

    monkeypatch.setattr(openvr, "isRuntimeInstalled", lambda: True)
    monkeypatch.setattr(openvr, "isHmdPresent", lambda: True)
    monkeypatch.setattr(openvr, "init", fail)
    report = probe.run_probe()
    assert report.init_error == "InitError_Init_NoServerForBackgroundApp"
    assert report.devices == []


def test_run_probe_enumerates_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    system = FakeSystem({0: openvr.TrackedDeviceClass_HMD, 3: openvr.TrackedDeviceClass_Controller})
    camera = FakeCamera([FakeHeader(1), FakeHeader(2)], no_camera_indices=frozenset({3}))
    monkeypatch.setattr(openvr, "isRuntimeInstalled", lambda: True)
    monkeypatch.setattr(openvr, "isHmdPresent", lambda: True)
    monkeypatch.setattr(openvr, "init", lambda app_type: system)
    monkeypatch.setattr(openvr, "shutdown", lambda: calls.append("shutdown"))
    monkeypatch.setattr(openvr, "VRTrackedCamera", lambda: camera)

    report = probe.run_probe(seconds=0.05)
    assert calls == ["shutdown"]
    assert [(d.index, d.device_class, d.has_camera) for d in report.devices] == [
        (0, "HMD", True),
        (3, "Controller", False),
    ]
    hmd = report.devices[0]
    assert hmd.model == "Steam Frame"
    assert hmd.serial is None  # property error is tolerated
    assert hmd.num_cameras == 4
    assert hmd.frame_layout == "Stereo"
    assert [fs.frame_type for fs in hmd.frame_sizes] == [
        "distorted",
        "undistorted",
        "max-undistorted",
    ]
    assert hmd.stream is not None
    assert hmd.stream.distinct_frames == 2
    assert verdict(report)[0] is Verdict.AVAILABLE


@pytest.mark.parametrize(
    ("report", "code"),
    [
        (ProbeReport(True, True, devices=[_hmd(_stream(30, 80.0))]), 0),
        (ProbeReport(True, True, devices=[_hmd(has_camera=False)]), 1),
        (ProbeReport(True, True, init_error="X"), 2),
    ],
)
def test_main_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    report: ProbeReport,
    code: int,
) -> None:
    monkeypatch.setattr(probe, "run_probe", lambda *a: report)
    assert probe.main([]) == code
    assert "VERDICT:" in capsys.readouterr().out


def test_main_json_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = ProbeReport(True, True, devices=[_hmd(_stream(30, 80.0))])
    monkeypatch.setattr(probe, "run_probe", lambda *a: report)
    assert probe.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "available"
    assert payload["devices"][0]["stream"]["distinct_frames"] == 30


# --- hardware-in-the-loop ---------------------------------------------------------------------


@pytest.mark.hardware
def test_probe_reaches_a_conclusion_on_real_hardware(tmp_path: Path) -> None:
    """Phase 1 camera-access spike against a real Steam Frame.

    Prerequisites: SteamVR running on this PC with the Steam Frame connected (streamed or
    tethered) and awake, in a lit room with the front cameras uncovered.
    Procedure: none; wear or face the headset toward the room while the test runs.
    Pass criteria: the probe reaches a conclusive verdict (AVAILABLE or UNAVAILABLE). Record
    the printed report and verdict under Phase 1 in docs/ROADMAP.md.
    """
    report = probe.run_probe(seconds=5.0, save_frame=tmp_path / "frame.ppm")
    print(format_report(report))
    if report.init_error or not report.runtime_installed:
        pytest.skip(f"SteamVR not reachable: {verdict(report)[1]}")
    assert verdict(report)[0] is not Verdict.INCONCLUSIVE, verdict(report)[1]
