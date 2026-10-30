"""Phase 1 camera-access spike.

Answers one question: can a PC-side OpenVR app read the Steam Frame's passthrough camera
frames (via ``IVRTrackedCamera``)? The answer decides between the single-PC architecture
and the alternatives listed under Phase 1 in docs/ROADMAP.md.

Connects as a background app, so it never starts SteamVR; start SteamVR with the headset
connected first. Run with ``uv run yokemapping-steamvr-probe --help``.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import openvr

# Frames whose mean RGB value is below this are treated as black (camera off/covered).
BLACK_FRAME_THRESHOLD = 2.0
POLL_INTERVAL_S = 0.016

FRAME_TYPES = {
    "distorted": openvr.VRTrackedCameraFrameType_Distorted,
    "undistorted": openvr.VRTrackedCameraFrameType_Undistorted,
    "max-undistorted": openvr.VRTrackedCameraFrameType_MaximumUndistorted,
}

_DEVICE_CLASS_NAMES = {
    getattr(openvr, name): name.removeprefix("TrackedDeviceClass_")
    for name in dir(openvr)
    if name.startswith("TrackedDeviceClass_") and name != "TrackedDeviceClass_Max"
}

_FRAME_LAYOUT_NAMES = {
    getattr(openvr, name): name.removeprefix("EVRTrackedCameraFrameLayout_")
    for name in dir(openvr)
    if name.startswith("EVRTrackedCameraFrameLayout_")
}


class Verdict(Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INCONCLUSIVE = "inconclusive"


@dataclass
class FrameSize:
    frame_type: str
    width: int
    height: int
    buffer_size: int


@dataclass
class StreamResult:
    frame_type: str
    duration_s: float
    frames_read: int = 0
    distinct_frames: int = 0
    width: int = 0
    height: int = 0
    bytes_per_pixel: int = 0
    mean_brightness: float | None = None
    saved_frame: str | None = None
    error: str | None = None

    @property
    def fps(self) -> float:
        return self.distinct_frames / self.duration_s if self.duration_s > 0 else 0.0


@dataclass
class DeviceReport:
    index: int
    device_class: str
    model: str | None = None
    serial: str | None = None
    has_camera: bool = False
    num_cameras: int | None = None
    frame_layout: str | None = None
    frame_sizes: list[FrameSize] = field(default_factory=list)
    stream: StreamResult | None = None
    error: str | None = None


@dataclass
class ProbeReport:
    runtime_installed: bool
    hmd_present: bool
    init_error: str | None = None
    devices: list[DeviceReport] = field(default_factory=list)


def mean_brightness(rgba: bytes, bytes_per_pixel: int = 4, max_samples: int = 100_000) -> float:
    """Mean of the R, G and B channels over an evenly-strided sample of pixels."""
    pixels = len(rgba) // bytes_per_pixel
    if pixels == 0:
        return 0.0
    stride = max(1, pixels // max_samples) * bytes_per_pixel
    channels = min(3, bytes_per_pixel)
    samples = [rgba[c::stride] for c in range(channels)]
    count = sum(len(s) for s in samples)
    return sum(sum(s) for s in samples) / count


def write_ppm(path: Path, rgba: bytes, width: int, height: int) -> None:
    """Save an RGBA buffer as a binary PPM (opens in GIMP, IrfanView, etc.)."""
    pixels = width * height
    if len(rgba) < pixels * 4:
        raise ValueError(f"buffer has {len(rgba)} bytes, expected at least {pixels * 4}")
    rgb = bytearray(pixels * 3)
    for c in range(3):
        rgb[c::3] = rgba[c : pixels * 4 : 4]
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(rgb))


def _camera_errors(devices: Sequence[DeviceReport]) -> list[str]:
    errors: set[str] = set()
    for d in devices:
        if d.error:
            errors.add(d.error)
        if d.stream is not None and d.stream.error:
            errors.add(d.stream.error)
    return sorted(errors)


def verdict(report: ProbeReport) -> tuple[Verdict, str]:
    if not report.runtime_installed:
        return Verdict.INCONCLUSIVE, "SteamVR runtime is not installed."
    if report.init_error:
        return (
            Verdict.INCONCLUSIVE,
            f"Could not connect to SteamVR ({report.init_error}). Start SteamVR with the "
            "Steam Frame connected and re-run.",
        )
    if not any(d.device_class == "HMD" for d in report.devices):
        return Verdict.INCONCLUSIVE, "SteamVR is running but no headset (HMD) is connected."

    camera_devices = [d for d in report.devices if d.has_camera]
    if not camera_devices:
        return (
            Verdict.UNAVAILABLE,
            "No tracked device reports a camera to PC-side OpenVR. If SteamVR Settings > "
            "Camera has an enable option, turn it on and re-run; otherwise camera frames are "
            "not exposed to PC apps.",
        )

    live = [
        d.stream for d in camera_devices if d.stream is not None and d.stream.distinct_frames >= 2
    ]
    lit = [
        s
        for s in live
        if s.mean_brightness is not None and s.mean_brightness >= BLACK_FRAME_THRESHOLD
    ]
    if lit:
        best = max(lit, key=lambda s: s.fps)
        return (
            Verdict.AVAILABLE,
            f"PC-side camera frames are available: {best.width}x{best.height} "
            f"({best.frame_type}) at ~{best.fps:.0f} fps.",
        )
    if live:
        return (
            Verdict.INCONCLUSIVE,
            "Frames arrive but are black. Check that the cameras are uncovered, the room is lit, "
            "and the camera is enabled in SteamVR settings, then re-run.",
        )
    errors = _camera_errors(camera_devices)
    detail = f" Errors: {', '.join(errors)}." if errors else ""
    return (
        Verdict.UNAVAILABLE,
        "A camera is reported but no frames could be streamed to the PC." + detail,
    )


def format_report(report: ProbeReport) -> str:
    lines = [
        "SteamVR camera-access probe",
        f"  runtime installed: {report.runtime_installed}",
        f"  hmd present:       {report.hmd_present}",
    ]
    if report.init_error:
        lines.append(f"  init error:        {report.init_error}")
    for d in report.devices:
        lines.append(
            f"  [{d.index}] {d.device_class:<16} model={d.model!s} serial={d.serial!s} "
            f"has_camera={d.has_camera}"
        )
        if d.num_cameras is not None or d.frame_layout:
            lines.append(f"       cameras={d.num_cameras} layout={d.frame_layout}")
        for fs in d.frame_sizes:
            lines.append(
                f"       {fs.frame_type:<16} {fs.width}x{fs.height} ({fs.buffer_size} bytes)"
            )
        if d.stream:
            s = d.stream
            lines.append(
                f"       stream {s.frame_type}: {s.distinct_frames} distinct / {s.frames_read} "
                f"read in {s.duration_s:.1f}s (~{s.fps:.1f} fps), brightness={s.mean_brightness}"
            )
            if s.saved_frame:
                lines.append(f"       saved frame: {s.saved_frame}")
            if s.error:
                lines.append(f"       stream error: {s.error}")
        if d.error:
            lines.append(f"       error: {d.error}")
    result, explanation = verdict(report)
    lines.append(f"VERDICT: {result.value.upper()} - {explanation}")
    return "\n".join(lines)


def _error_name(exc: BaseException) -> str:
    return type(exc).__name__


def _prop(getter: Any, index: int, prop: int) -> Any:
    try:
        return getter(index, prop)
    except openvr.error_code.OpenVRError:
        return None


def _stream(
    camera: Any, index: int, frame_type_name: str, seconds: float, save_frame: Path | None
) -> StreamResult:
    frame_type = FRAME_TYPES[frame_type_name]
    result = StreamResult(frame_type=frame_type_name, duration_s=seconds)
    try:
        handle = camera.acquireVideoStreamingService(index)
    except openvr.error_code.OpenVRError as exc:
        result.error = f"acquireVideoStreamingService: {_error_name(exc)}"
        return result
    try:
        _, _, size = camera.getCameraFrameSize(index, frame_type)
        buffer = (ctypes.c_uint8 * size)()
        sequences: set[int] = set()
        last_header: Any = None
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                last_header = camera.getVideoStreamFrameBuffer(handle, frame_type, buffer, size)
            except openvr.error_code.TrackedCameraError_NoFrameAvailable:
                pass  # camera spinning up; keep polling
            except openvr.error_code.OpenVRError as exc:
                result.error = f"getVideoStreamFrameBuffer: {_error_name(exc)}"
                break
            else:
                result.frames_read += 1
                sequences.add(int(last_header.nFrameSequence))
            time.sleep(POLL_INTERVAL_S)
        result.distinct_frames = len(sequences)
        if last_header is not None:
            result.width = int(last_header.nWidth)
            result.height = int(last_header.nHeight)
            result.bytes_per_pixel = int(last_header.nBytesPerPixel)
            data = bytes(buffer)
            result.mean_brightness = round(mean_brightness(data, result.bytes_per_pixel), 2)
            if save_frame is not None and result.bytes_per_pixel == 4:
                path = save_frame.with_name(f"{save_frame.stem}_dev{index}{save_frame.suffix}")
                write_ppm(path, data, result.width, result.height)
                result.saved_frame = str(path)
    except openvr.error_code.OpenVRError as exc:
        result.error = result.error or _error_name(exc)
    finally:
        camera.releaseVideoStreamingService(handle)
    return result


def _probe_device(
    system: Any,
    camera: Any,
    index: int,
    device_class: int,
    frame_type: str,
    seconds: float,
    save_frame: Path | None,
) -> DeviceReport:
    get_str = system.getStringTrackedDeviceProperty
    get_int = system.getInt32TrackedDeviceProperty
    report = DeviceReport(
        index=index,
        device_class=_DEVICE_CLASS_NAMES.get(device_class, str(device_class)),
        model=_prop(get_str, index, openvr.Prop_ModelNumber_String),
        serial=_prop(get_str, index, openvr.Prop_SerialNumber_String),
    )
    try:
        report.has_camera = bool(camera.hasCamera(index).value)
    except openvr.error_code.OpenVRError as exc:
        report.error = f"hasCamera: {_error_name(exc)}"
        return report
    if not report.has_camera:
        return report

    report.num_cameras = _prop(get_int, index, openvr.Prop_NumCameras_Int32)
    layout = _prop(get_int, index, openvr.Prop_CameraFrameLayout_Int32)
    if layout is not None:
        report.frame_layout = _FRAME_LAYOUT_NAMES.get(layout, str(layout))
    for name, value in FRAME_TYPES.items():
        try:
            width, height, size = camera.getCameraFrameSize(index, value)
        except openvr.error_code.OpenVRError as exc:
            report.error = f"getCameraFrameSize({name}): {_error_name(exc)}"
            continue
        report.frame_sizes.append(FrameSize(name, int(width), int(height), int(size)))
    report.stream = _stream(camera, index, frame_type, seconds, save_frame)
    return report


def run_probe(
    seconds: float = 3.0, frame_type: str = "distorted", save_frame: Path | None = None
) -> ProbeReport:
    """Query SteamVR for camera-capable devices and try to stream frames from each."""
    if not openvr.isRuntimeInstalled():
        return ProbeReport(runtime_installed=False, hmd_present=False)
    report = ProbeReport(runtime_installed=True, hmd_present=bool(openvr.isHmdPresent()))
    try:
        system = openvr.init(openvr.VRApplication_Background)
    except openvr.error_code.OpenVRError as exc:
        report.init_error = _error_name(exc)
        return report
    try:
        camera = openvr.VRTrackedCamera()
        for index in range(openvr.k_unMaxTrackedDeviceCount):
            device_class = system.getTrackedDeviceClass(index)
            if device_class == openvr.TrackedDeviceClass_Invalid:
                continue
            report.devices.append(
                _probe_device(system, camera, index, device_class, frame_type, seconds, save_frame)
            )
    finally:
        openvr.shutdown()
    return report


_EXIT_CODES = {Verdict.AVAILABLE: 0, Verdict.UNAVAILABLE: 1, Verdict.INCONCLUSIVE: 2}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="yokemapping-steamvr-probe",
        description=(
            "Check whether PC-side OpenVR can read the headset's passthrough camera frames. "
            "Start SteamVR with the headset connected first. Exit code: 0 available, "
            "1 unavailable, 2 inconclusive."
        ),
    )
    parser.add_argument(
        "--seconds", type=float, default=3.0, help="how long to stream frames (default: 3)"
    )
    parser.add_argument(
        "--frame-type",
        choices=sorted(FRAME_TYPES),
        default="distorted",
        help="frame type to stream (default: distorted)",
    )
    parser.add_argument(
        "--save-frame",
        type=Path,
        metavar="PATH.ppm",
        help="save the last streamed frame per camera device as a PPM image",
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    report = run_probe(args.seconds, args.frame_type, args.save_frame)
    result, explanation = verdict(report)
    if args.json:
        payload = {**asdict(report), "verdict": result.value, "explanation": explanation}
        print(json.dumps(payload, indent=2))
    else:
        print(format_report(report))
    return _EXIT_CODES[result]


if __name__ == "__main__":
    sys.exit(main())
