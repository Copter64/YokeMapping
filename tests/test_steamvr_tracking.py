from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any

import openvr
import pytest

from yokemapping.probes import pose_monitor
from yokemapping.probes.pose_monitor import format_summary, run_monitor
from yokemapping.tracking.pose import Pose, PoseSample, TrackingStatus
from yokemapping.tracking.steamvr import (
    SERIAL_RESCAN_INTERVAL_S,
    DeviceInfo,
    SteamVRTracker,
    validate_selector,
)

LEFT = openvr.TrackedControllerRole_LeftHand
RIGHT = openvr.TrackedControllerRole_RightHand
NO_ROLE = openvr.TrackedControllerRole_Invalid


@dataclass
class FakeDevice:
    device_class: int
    serial: str
    model: str = "model"
    role: int = NO_ROLE
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    valid: bool = True
    connected: bool = True


class FakeSystem:
    def __init__(self, devices: dict[int, FakeDevice] | None = None) -> None:
        self.devices = devices or {}
        self.events: list[int] = []
        self.acknowledged = False
        self.class_queries = 0

    def pollNextEvent(self, event: Any) -> bool:
        if not self.events:
            return False
        event.eventType = self.events.pop(0)
        return True

    def acknowledgeQuit_Exiting(self) -> None:
        self.acknowledged = True

    def getDeviceToAbsoluteTrackingPose(self, origin: int, predict: float, poses: Any) -> Any:
        for i in range(len(poses)):
            p = poses[i]
            d = self.devices.get(i)
            p.bDeviceIsConnected = bool(d and d.connected)
            p.bPoseIsValid = bool(d and d.valid)
            m = p.mDeviceToAbsoluteTracking.m
            for r in range(3):
                for c in range(3):
                    m[r][c] = 1.0 if r == c else 0.0
                m[r][3] = d.position[r] if d else 0.0
        return poses

    def getTrackedDeviceIndexForControllerRole(self, role: int) -> int:
        for index, d in self.devices.items():
            if d.role == role:
                return index
        return int(openvr.k_unTrackedDeviceIndexInvalid)

    def getControllerRoleForTrackedDeviceIndex(self, index: int) -> int:
        d = self.devices.get(index)
        return d.role if d else NO_ROLE

    def getTrackedDeviceClass(self, index: int) -> int:
        self.class_queries += 1
        d = self.devices.get(index)
        return d.device_class if d else int(openvr.TrackedDeviceClass_Invalid)

    def getStringTrackedDeviceProperty(self, index: int, prop: int) -> str:
        d = self.devices[index]
        if prop == openvr.Prop_SerialNumber_String:
            return d.serial
        if prop == openvr.Prop_ModelNumber_String:
            return d.model
        raise openvr.error_code.TrackedProp_UnknownProperty(0)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.001)


class Harness:
    def __init__(self, system: FakeSystem, *, fail_connects: int = 0) -> None:
        self.system = system
        self.fail_connects = fail_connects
        self.connects = 0
        self.disconnects = 0
        self.clock = Clock()
        self.tracker = SteamVRTracker(
            retry_interval_s=2.0,
            connect=self.connect,
            disconnect=self.disconnect,
            clock=self.clock,
        )

    def connect(self) -> FakeSystem:
        self.connects += 1
        if self.fail_connects:
            self.fail_connects -= 1
            raise openvr.error_code.InitError_Init_NoServerForBackgroundApp(0)
        return self.system

    def disconnect(self) -> None:
        self.disconnects += 1

    def tick(self, selector: str) -> PoseSample:
        self.tracker.update()
        return self.tracker.sample(selector)


def _hmd(**kw: Any) -> FakeDevice:
    return FakeDevice(int(openvr.TrackedDeviceClass_HMD), "HMD-1", **kw)


def _controller(serial: str, **kw: Any) -> FakeDevice:
    return FakeDevice(int(openvr.TrackedDeviceClass_Controller), serial, **kw)


# --- selectors ------------------------------------------------------------------------------


@pytest.mark.parametrize("selector", ["hmd", "left", "right", "serial:ABC123"])
def test_valid_selectors(selector: str) -> None:
    assert validate_selector(selector) == selector


@pytest.mark.parametrize("selector", ["", "HMD", "head", "serial:", "LHR-1234"])
def test_invalid_selectors(selector: str) -> None:
    with pytest.raises(ValueError, match="invalid device selector"):
        validate_selector(selector)


# --- tracker --------------------------------------------------------------------------------


def test_hmd_pose_ok() -> None:
    h = Harness(FakeSystem({0: _hmd(position=(0.1, 1.6, -0.2))}))
    sample = h.tick("hmd")
    assert sample.status is TrackingStatus.OK
    assert sample.pose is not None
    assert sample.pose.position == pytest.approx((0.1, 1.6, -0.2))
    assert sample.pose.orientation == pytest.approx((1, 0, 0, 0))
    assert sample.pose.timestamp == h.clock.now


def test_runtime_unavailable_then_retry_after_interval() -> None:
    h = Harness(FakeSystem({0: _hmd()}), fail_connects=1)
    assert h.tick("hmd").status is TrackingStatus.RUNTIME_UNAVAILABLE
    h.clock.now += 1.0
    assert h.tick("hmd").status is TrackingStatus.RUNTIME_UNAVAILABLE
    assert h.connects == 1  # no retry before the interval elapses
    h.clock.now += 1.5
    assert h.tick("hmd").status is TrackingStatus.OK
    assert h.connects == 2


def test_tracking_lost_and_device_not_found() -> None:
    system = FakeSystem({0: _hmd(valid=False)})
    h = Harness(system)
    assert h.tick("hmd").status is TrackingStatus.TRACKING_LOST
    system.devices[0].connected = False
    assert h.tick("hmd").status is TrackingStatus.DEVICE_NOT_FOUND


def test_hand_roles() -> None:
    system = FakeSystem({0: _hmd(), 1: _controller("C-1", role=RIGHT, position=(0.3, 1.0, -0.4))})
    h = Harness(system)
    assert h.tick("left").status is TrackingStatus.DEVICE_NOT_FOUND
    right = h.tick("right")
    assert right.pose is not None
    assert right.pose.position == pytest.approx((0.3, 1.0, -0.4))
    system.devices[1].role = LEFT
    assert h.tick("left").status is TrackingStatus.OK
    assert h.tick("right").status is TrackingStatus.DEVICE_NOT_FOUND


def test_serial_lookup_is_cached_until_device_events() -> None:
    system = FakeSystem({0: _hmd(), 3: _controller("YOKE-1", position=(0.0, 0.9, -0.5))})
    h = Harness(system)
    assert h.tick("serial:YOKE-1").status is TrackingStatus.OK
    queries = system.class_queries
    assert h.tick("serial:YOKE-1").status is TrackingStatus.OK
    assert system.class_queries == queries  # cached

    # Device moved to another index; an activation event invalidates the cache.
    system.devices[5] = system.devices.pop(3)
    system.events.append(openvr.VREvent_TrackedDeviceActivated)
    assert h.tick("serial:YOKE-1").status is TrackingStatus.OK
    assert system.class_queries > queries


def test_missing_serial_rescans_are_rate_limited() -> None:
    system = FakeSystem({0: _hmd()})
    h = Harness(system)
    assert h.tick("serial:YOKE-1").status is TrackingStatus.DEVICE_NOT_FOUND
    queries = system.class_queries
    h.clock.now += SERIAL_RESCAN_INTERVAL_S / 2
    assert h.tick("serial:YOKE-1").status is TrackingStatus.DEVICE_NOT_FOUND
    assert system.class_queries == queries
    system.devices[2] = _controller("YOKE-1")
    h.clock.now += SERIAL_RESCAN_INTERVAL_S
    assert h.tick("serial:YOKE-1").status is TrackingStatus.OK


def test_cached_serial_device_disconnecting_is_not_found() -> None:
    system = FakeSystem({0: _hmd(), 3: _controller("YOKE-1")})
    h = Harness(system)
    assert h.tick("serial:YOKE-1").status is TrackingStatus.OK
    system.devices[3].connected = False
    assert h.tick("serial:YOKE-1").status is TrackingStatus.DEVICE_NOT_FOUND


def test_quit_event_disconnects_and_reconnects_later() -> None:
    system = FakeSystem({0: _hmd()})
    h = Harness(system)
    assert h.tick("hmd").status is TrackingStatus.OK
    system.events.append(openvr.VREvent_Quit)
    assert h.tick("hmd").status is TrackingStatus.RUNTIME_UNAVAILABLE
    assert system.acknowledged
    assert h.disconnects == 1
    assert not h.tracker.connected
    h.clock.now += 2.5
    assert h.tick("hmd").status is TrackingStatus.OK
    assert h.connects == 2


def test_sample_before_update_is_unavailable() -> None:
    h = Harness(FakeSystem({0: _hmd()}))
    assert h.tracker.sample("hmd").status is TrackingStatus.RUNTIME_UNAVAILABLE


def test_devices_listing() -> None:
    system = FakeSystem({0: _hmd(), 2: _controller("C-2", role=LEFT, model="Frame Controller")})
    h = Harness(system)
    assert h.tracker.devices() == []
    h.tracker.update()
    assert h.tracker.devices() == [
        DeviceInfo(0, "HMD", "model", "HMD-1", None),
        DeviceInfo(2, "Controller", "Frame Controller", "C-2", "left"),
    ]


def test_close_disconnects_once() -> None:
    h = Harness(FakeSystem({0: _hmd()}))
    h.tracker.close()
    assert h.disconnects == 0
    h.tracker.update()
    h.tracker.close()
    h.tracker.close()
    assert h.disconnects == 1


def test_status_transitions_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    system = FakeSystem({0: _hmd()})
    h = Harness(system)
    with caplog.at_level(logging.INFO, logger="yokemapping.tracking.steamvr"):
        h.tick("hmd")
        h.tick("hmd")
        system.devices[0].valid = False
        h.tick("hmd")
    messages = [(r.levelno, r.getMessage()) for r in caplog.records if "hmd:" in r.getMessage()]
    assert messages == [(logging.INFO, "hmd: ok"), (logging.WARNING, "hmd: tracking_lost")]


# --- monitor --------------------------------------------------------------------------------


class ScriptedTracker:
    """Returns OK for the first half of the samples, then TRACKING_LOST."""

    def __init__(self, ok_samples: int) -> None:
        self.ok_samples = ok_samples
        self.count = 0

    def update(self) -> bool:
        return True

    def sample(self, selector: str) -> PoseSample:
        self.count += 1
        if self.count <= self.ok_samples:
            return PoseSample(TrackingStatus.OK, Pose((0.0, 1.0, 0.0), (1.0, 0, 0, 0), 0.0))
        return PoseSample(TrackingStatus.TRACKING_LOST)


def test_run_monitor_collects_stats_and_reports() -> None:
    clock = Clock()
    out = io.StringIO()
    stats = run_monitor(
        ScriptedTracker(ok_samples=50),
        ["hmd"],
        seconds=2.0,
        rate_hz=50.0,
        out=out,
        clock=clock,
        sleep=clock.sleep,
    )
    st = stats["hmd"]
    assert st.samples == pytest.approx(100, abs=2)
    assert st.statuses[TrackingStatus.OK] == 50
    assert st.transitions == 1
    assert st.ok_fraction == pytest.approx(0.5, abs=0.02)
    report = out.getvalue()
    assert "hmd" in report and "Hz" in report and "pos=(+0.000, +1.000, +0.000)" in report
    summary = format_summary(stats, 2.0)
    assert "status changes=1" in summary and "tracking_lost=" in summary


class FakeTrackerForMain(ScriptedTracker):
    def __init__(self, *, connected: bool = True, devices: list[DeviceInfo] | None = None) -> None:
        super().__init__(ok_samples=10**9)
        self._connected = connected
        self._devices = devices or []
        self.closed = False

    def update(self) -> bool:
        return self._connected

    def devices(self) -> list[DeviceInfo]:
        return self._devices

    def close(self) -> None:
        self.closed = True


def test_main_list_devices(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeTrackerForMain(devices=[DeviceInfo(3, "Controller", "M", "YOKE-1", "right")])
    monkeypatch.setattr(pose_monitor, "SteamVRTracker", lambda: fake)
    assert pose_monitor.main(["--list"]) == 0
    assert "serial=YOKE-1" in capsys.readouterr().out
    assert fake.closed


def test_main_list_without_steamvr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pose_monitor, "SteamVRTracker", lambda: FakeTrackerForMain(connected=False))
    assert pose_monitor.main(["--list"]) == 2


def test_main_monitor_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pose_monitor, "SteamVRTracker", lambda: FakeTrackerForMain())
    assert pose_monitor.main(["--device", "hmd", "--seconds", "0.05", "--rate", "200"]) == 0
    assert "Summary over" in capsys.readouterr().out


def test_main_rejects_bad_selector() -> None:
    with pytest.raises(SystemExit):
        pose_monitor.main(["--device", "head"])


# --- hardware-in-the-loop ---------------------------------------------------------------------


@pytest.mark.hardware
def test_hmd_pose_stream_on_real_hardware() -> None:
    """Phase 1 pose stream against a real Steam Frame.

    Prerequisites: SteamVR running on this PC with the Steam Frame connected, awake, and
    tracking (worn, or proximity sensor covered so it doesn't sleep).
    Procedure: none; keep the headset tracking for ~5 seconds.
    Pass criteria: HMD pose is OK for >= 95% of samples at an effective rate >= 60 Hz.
    """
    tracker = SteamVRTracker()
    try:
        if not tracker.update():
            pytest.skip("SteamVR not running/reachable")
        stats = run_monitor(tracker, ["hmd"], seconds=5.0, rate_hz=90.0)
    finally:
        tracker.close()
    st = stats["hmd"]
    print(format_summary(stats, 5.0))
    assert st.ok_fraction >= 0.95, format_summary(stats, 5.0)
    assert st.statuses[TrackingStatus.OK] / 5.0 >= 60
