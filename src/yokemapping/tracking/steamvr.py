"""SteamVR (OpenVR) device pose tracking with automatic reconnect.

One ``SteamVRTracker`` per process owns the OpenVR connection. Call ``update()`` once per
tick, then ``sample(selector)`` for each device of interest. Selectors:

- ``"hmd"`` — the headset
- ``"left"`` / ``"right"`` — controllers by their current hand role
- ``"serial:<SERIAL>"`` — a specific device, e.g. a controller strapped to the yoke, so it
  stays identified even if SteamVR reassigns hand roles
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import openvr

from yokemapping.tracking.pose import PoseSample, TrackingStatus, pose_from_matrix

log = logging.getLogger(__name__)

SERIAL_PREFIX = "serial:"
SERIAL_RESCAN_INTERVAL_S = 1.0
_ROLES = {
    "left": openvr.TrackedControllerRole_LeftHand,
    "right": openvr.TrackedControllerRole_RightHand,
}
_RESOLVE_EVENTS = {
    openvr.VREvent_TrackedDeviceActivated,
    openvr.VREvent_TrackedDeviceDeactivated,
    openvr.VREvent_TrackedDeviceRoleChanged,
}


def validate_selector(selector: str) -> str:
    if selector in ("hmd", *_ROLES) or (
        selector.startswith(SERIAL_PREFIX) and len(selector) > len(SERIAL_PREFIX)
    ):
        return selector
    raise ValueError(f"invalid device selector {selector!r}; use hmd, left, right, or serial:<id>")


def _default_connect() -> Any:
    return openvr.init(openvr.VRApplication_Background)


_DEVICE_CLASS_NAMES = {
    getattr(openvr, name): name.removeprefix("TrackedDeviceClass_")
    for name in dir(openvr)
    if name.startswith("TrackedDeviceClass_") and name != "TrackedDeviceClass_Max"
}
_ROLE_NAMES = {value: name for name, value in _ROLES.items()}


@dataclass(frozen=True)
class DeviceInfo:
    index: int
    device_class: str
    model: str | None
    serial: str | None
    role: str | None  # "left"/"right" for hand-assigned controllers


class SteamVRTracker:
    def __init__(
        self,
        *,
        retry_interval_s: float = 2.0,
        connect: Callable[[], Any] = _default_connect,
        disconnect: Callable[[], None] = openvr.shutdown,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._retry_interval_s = retry_interval_s
        self._connect = connect
        self._disconnect = disconnect
        self._clock = clock
        self._system: Any = None
        self._next_retry = 0.0
        self._poses = (openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()
        self._event = openvr.VREvent_t()
        self._serial_cache: dict[str, int] = {}
        self._serial_miss_until: dict[str, float] = {}
        self._updated_at: float | None = None
        self._last_status: dict[str, TrackingStatus] = {}

    @property
    def connected(self) -> bool:
        return self._system is not None

    def update(self) -> bool:
        """Connect if needed, process SteamVR events, and fetch all device poses.

        Returns True if fresh poses are available for ``sample()``.
        """
        self._updated_at = None
        now = self._clock()
        if self._system is None:
            if now < self._next_retry:
                return False
            try:
                self._system = self._connect()
            except openvr.error_code.OpenVRError as exc:
                self._next_retry = now + self._retry_interval_s
                log.debug("SteamVR not available (%s); retrying", type(exc).__name__)
                return False
            self._serial_cache.clear()
            log.info("Connected to SteamVR")

        while self._system.pollNextEvent(self._event):
            event_type = self._event.eventType
            if event_type == openvr.VREvent_Quit:
                log.info("SteamVR is quitting; disconnecting")
                self._system.acknowledgeQuit_Exiting()
                self._drop_connection(now)
                return False
            if event_type in _RESOLVE_EVENTS:
                self._serial_cache.clear()
                self._serial_miss_until.clear()

        self._system.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding, 0.0, self._poses
        )
        self._updated_at = now
        return True

    def sample(self, selector: str) -> PoseSample:
        if self._system is None or self._updated_at is None:
            result = PoseSample(TrackingStatus.RUNTIME_UNAVAILABLE)
        else:
            index = self._resolve(selector)
            raw = None if index is None else self._poses[index]
            if raw is None or not raw.bDeviceIsConnected:
                self._serial_cache.pop(selector, None)
                result = PoseSample(TrackingStatus.DEVICE_NOT_FOUND)
            elif not raw.bPoseIsValid:
                result = PoseSample(TrackingStatus.TRACKING_LOST)
            else:
                pose = pose_from_matrix(raw.mDeviceToAbsoluteTracking.m, self._updated_at)
                result = PoseSample(TrackingStatus.OK, pose)
        self._log_transition(selector, result.status)
        return result

    def devices(self) -> list[DeviceInfo]:
        """List currently known tracked devices (empty if not connected)."""
        if self._system is None:
            return []
        found: list[DeviceInfo] = []
        for index in range(openvr.k_unMaxTrackedDeviceCount):
            device_class = self._system.getTrackedDeviceClass(index)
            if device_class == openvr.TrackedDeviceClass_Invalid:
                continue
            role = self._system.getControllerRoleForTrackedDeviceIndex(index)
            found.append(
                DeviceInfo(
                    index=index,
                    device_class=_DEVICE_CLASS_NAMES.get(device_class, str(device_class)),
                    model=self._string_prop(index, openvr.Prop_ModelNumber_String),
                    serial=self._string_prop(index, openvr.Prop_SerialNumber_String),
                    role=_ROLE_NAMES.get(role),
                )
            )
        return found

    def close(self) -> None:
        if self._system is not None:
            self._drop_connection(self._clock())

    def _string_prop(self, index: int, prop: int) -> str | None:
        try:
            return str(self._system.getStringTrackedDeviceProperty(index, prop))
        except openvr.error_code.OpenVRError:
            return None

    def _drop_connection(self, now: float) -> None:
        self._system = None
        self._updated_at = None
        self._serial_cache.clear()
        self._serial_miss_until.clear()
        self._next_retry = now + self._retry_interval_s
        self._disconnect()

    def _resolve(self, selector: str) -> int | None:
        if selector == "hmd":
            return int(openvr.k_unTrackedDeviceIndex_Hmd)
        if selector in _ROLES:
            index = int(self._system.getTrackedDeviceIndexForControllerRole(_ROLES[selector]))
            return None if index == openvr.k_unTrackedDeviceIndexInvalid else index
        if selector in self._serial_cache:
            return self._serial_cache[selector]
        if self._clock() < self._serial_miss_until.get(selector, 0.0):
            return None
        serial = selector.removeprefix(SERIAL_PREFIX)
        for index in range(openvr.k_unMaxTrackedDeviceCount):
            if self._system.getTrackedDeviceClass(index) == openvr.TrackedDeviceClass_Invalid:
                continue
            if self._string_prop(index, openvr.Prop_SerialNumber_String) == serial:
                self._serial_cache[selector] = index
                return index
        self._serial_miss_until[selector] = self._clock() + SERIAL_RESCAN_INTERVAL_S
        return None

    def _log_transition(self, selector: str, status: TrackingStatus) -> None:
        previous = self._last_status.get(selector)
        if status is previous:
            return
        self._last_status[selector] = status
        level = logging.INFO if status is TrackingStatus.OK else logging.WARNING
        if previous is None and status is not TrackingStatus.OK:
            level = logging.INFO
        log.log(level, "%s: %s", selector, status.value)
