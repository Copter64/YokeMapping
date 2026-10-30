# YokeMapping Project Roadmap

## Overview

YokeMapping is a Windows PC application that lets a real-world flight yoke control
the virtual yoke in **Microsoft Flight Simulator 2024 (MSFS2024)**, viewed through
a **Valve Steam Frame** VR headset. The app runs as a single process on the same
Windows PC that runs MSFS2024:

- **Headset I/O** via SteamVR's **OpenVR** runtime: HMD and controller poses
  (`IVRSystem`), at 90 Hz on our unit. Passthrough camera frames are **not**
  exposed to PC apps (measured); see the shelved camera research track in
  Phase 1.
- **Flight sim I/O** via Microsoft's official **SimConnect SDK**, used to read
  (and eventually write) the virtual yoke/aileron/elevator axis values.
- **Registration (primary approach, chosen 2026-10-21):** use the **controllers
  as the calibration tool**: touch points on the real yoke (and on the virtual
  yoke in the sim), fit the yoke's axes, and align the two frames (Phase 2).
  Live yoke motion is expected to come from the Moza yoke's own USB axes.
- **Shelved research track:** markerless camera detection of the yoke via an
  on-headset capture stage. It works technically (60 fps) but the cameras only
  run while passthrough is visible. See Phase 1b and
  [STEAMFRAME_DEV_ACCESS.md](STEAMFRAME_DEV_ACCESS.md).

Primary language: **Python**. Key libraries: `openvr` (pyopenvr, in use),
`python-simconnect`/`SimConnect` (SimConnect bindings, to be chosen), and
NumPy for the registration maths (to be added).

> Steam Frame is new hardware (launched 2026). Its exact developer-facing OpenVR/
> OpenXR extensions may still be evolving. Always verify API surface against the
> live Valve OpenVR headers / SteamVR runtime installed, rather than assuming a
> fixed API from older Vive/Index documentation.

## Where we left off (updated 2026-10-29, late evening)

**Done:** Phase 0 (scaffolding); Phase 1a (PC-side pose streaming at 90 Hz,
camera-access spike); Phase 1b camera research (details below and in
[STEAMFRAME_DEV_ACCESS.md](STEAMFRAME_DEV_ACCESS.md)); **Phase 2 software**:
registration maths, yoke frame, alignment, hands-free capture tool
(`uv run yokemapping-capture`), JSON capture files; 162 unit tests (a full
simulated pivot, capture real, capture virtual, align flow recovers the known
transform) plus 3 hardware tests; `devices` verified on the live headset.

**Next: hands-on tests with real controllers (owner needed).** Runs H1–H4 in
[TESTING.md](TESTING.md): resting jitter, pivot calibration of the touch point,
two captures of the real Moza yoke (repeatability and a ruler check on the grip
spacing), then the virtual yoke in MSFS and the alignment. After those:
decide how to apply the offset in MSFS, and optionally add an overlay
wireframe and the SLAM stereo-click comparison. Phase 3 (SimConnect) can also
proceed in parallel (MSFS 2024 is installed).

**Caveats for the hands-on tests (design, not yet verified):**
- All points are in **SteamVR tracking space**. Capture the real and the virtual
  yoke in the **same SteamVR session**, and do **not** recenter MSFS or re-run
  room setup between them or afterwards, or the virtual yoke moves in that space
  and the alignment is stale.
- Use the **same physical point of the controller** for the pivot and for every
  touch. Touch the virtual yoke with the controller tip where the virtual
  landmark appears; depth perception limits accuracy there, so expect the
  virtual capture to be the weakest link.

**Camera research (summary; closed for now).** Live tracking-camera frames are
not reachable: the compositor's 8 shared 1056×1024 buffers stay empty, the live
1152×4608 buffers are video-decoder output, and the real SLAM frames are
private to XRService. The colour cameras run only while passthrough is visible.
A 2-second Valve recording showed the SLAM cameras **do see the yoke clearly**
in both left and right images (baseline 105.8 mm, axes 52° apart), so a
semi-manual **offline stereo registration** is plausible (est. 5–15 mm depth
error before pose and calibration error; not built). Details, results and tools
are in [STEAMFRAME_DEV_ACCESS.md](STEAMFRAME_DEV_ACCESS.md).

**Open questions for the owner:** (a) what the tracked pose should add, since
the Moza already reports its axes over USB (Phase 4); (b) how to apply the
real-to-virtual offset in MSFS (VR cockpit offset vs. SteamVR play-space shift);
(c) the capture layer was **reinstalled and enabled on 2026-10-29** for the
probe and is harmless but no longer needed; remove it when convenient; (d)
whether to build the SLAM stereo-click registration after the controller method
is validated.

**Pending small items:** the manual disconnect/restart run for Phase 1a.

## Where we left off — earlier camera notes (2026-10-29)

**Done earlier:** Phase 1b colour-camera research (works at 60 fps, but only while passthrough is visible); Phase 1b′
tracking-camera probe (**negative result**, below).

**Tried today (2026-10-29, owner request): identify the yoke with the headset's
*tracking* (SLAM) cameras.** Result: **live tracking-camera frames are not
reachable with our capture layer.**
- The compositor receives exactly one camera-sized buffer set besides the
  colour pair: 8 × 1056×1024 NV12 (linear). In SteamVR Home with room view off
  **all eight are zeros and never change**. Spyjacob's notes say the same
  ("SLAM-sized buffers exist but stay empty") and that XRService keeps the real
  SLAM frames (32 × 1152×1024 NV12) **private**, sharing none with the
  compositor or vrlink.
- The 16 × 1152×4608 buffers that *do* update live with passthrough off are
  almost certainly the **hardware video decoder's output for the Steam Link
  stream from the PC** (10-bit 2-plane UBWC, `vrlinkrunthread` holds the
  decoder `/dev/video22`, the headset's `vrlink` is connected to the PC). A
  single grabbed frame was garbled tiled data, not a camera image, and was
  deleted. Not tracking cameras.
- Remaining ways to reach the tracking cameras, none recommended for now:
  (a) Valve's own **dataset recorder** (`vrcmd --cmdhmd "start_tracking_recording …"`)
  writes SLAM video and poses to disk, **offline only**; usable for a one-shot
  registration experiment, not for live tracking (needs owner consent: it
  records the room). (b) the XRService IPC (`libArcturusPerception`), single
  session and risky to tracking. (c) intercepting XRService's private buffers,
  blocked by `ptrace_scope=1` and out of bounds.
- Colour cameras: live at 60 fps, but only while passthrough is visible.

**Conclusion:** live in-flight camera tracking of the yoke is not available
through any supported or low-risk route. The **controller-assisted
registration** plan (2026-10-21) is the primary approach.

**Update (2026-10-29, "2-second look", S1):** Valve's recorder produced
snapshots of every camera; the SLAM cameras (mono, 1056×1024, 8-bit, indoor
light is dim but usable) **clearly show the Moza yoke, the owner's hands on it
and the surrounding desk, in both left and right cameras** from the normal
seated position. So an **offline one-shot registration from SLAM snapshots is
plausible**: the two SLAM cameras are 105.8 mm apart with optical axes 52°
apart, which makes one snapshot a stereo pair with known calibration. Rough
accuracy estimate (pinhole approximation, not yet measured): ~5–15 mm depth
error and ~1.5–3 mm lateral error at 0.4–0.5 m for a 1–2 pixel clicking error,
before headset-pose and calibration error. Each snapshot folder also holds the
headset pose log (`shortTermRecorder_liveTrackedStates.ocsv`) and
`slamEvents.txt`. The LED-exposure ("-controller") frames are nearly black, as
expected. The images were viewed once and deleted; nothing is kept.

**Next, in order (superseded by the block at the top of this section):**
1. ~~Build the registration maths and capture CLI~~ — done 2026-10-29 (see top).
2. Phase 3 (SimConnect) can proceed in parallel (MSFS 2024 is installed).
3. **Camera-assisted one-shot registration (plausible):** record ~2–10 s
   with Valve's recorder while looking at the yoke and use the **SLAM stereo
   snapshots** (left + right at the same instant) plus the headset pose to
   locate yoke landmarks by triangulation. Start semi-manual (click landmarks in
   both images; a small PC tool does the fisheye model and triangulation, with
   synthetic tests), and validate it against the controller touch capture.
   Fully automatic yoke detection stays a later research item.

**Open questions for the owner (earlier list):** (a) what the tracked pose should add, since
the Moza already reports its axes over USB (Phase 4); (b) how to apply the
real-to-virtual offset in MSFS (VR cockpit offset vs. SteamVR play-space shift);
(c) the capture layer was **reinstalled and enabled on 2026-10-29** for the
probe and is harmless but no longer needed; remove it when convenient
([STEAMFRAME_DEV_ACCESS.md](STEAMFRAME_DEV_ACCESS.md)); (d) whether to try the
offline SLAM-recording experiment.

**Pending small items:** the manual disconnect/restart run for Phase 1a.

## Phase discipline

Each phase below must reach its **Exit criteria** — validated with a real,
repeatable manual (or scripted) test — before work starts on the next phase.
Update this document's **Status** field as phases progress
(`Not started` → `In progress` → `Done`) and tick the **Checklist** items
(`- [x]` done, `- [ ]` remaining) as work lands. Checklists summarise progress;
the Scope and Exit criteria sections remain the definition of done.

---

## Phase 0 — Project scaffolding

**Status:** Done (validated 2026-10-20)

**Checklist:**
- [x] Python 3.12 + uv project, `src/` layout, console script and `python -m` entry point
- [x] ruff (lint + format), mypy strict, pytest with an opt-in `hardware` marker
- [x] Validated settings: defaults < TOML < `.env` < `YOKEMAPPING_*` env vars
- [x] Logging to stderr plus optional rotating file
- [x] App starts and shuts down cleanly (exit codes 0 / 1 / 2)
- [x] Hardware-in-the-loop test conventions ([TESTING.md](TESTING.md))
- [x] Git repo, `.gitignore`, `.gitattributes`, `.env.example`, README
- [x] Roadmap, project instructions and five specialist agents

**Goal:** Establish the baseline project structure and developer workflow so
every later phase can be built, linted, and tested consistently.

**Scope:**
- Python project layout (`src/`, `tests/`, package metadata, dependency
  management e.g. `pyproject.toml`/`requirements.txt`).
- Linting/formatting setup (e.g. `ruff`/`black`) and a test runner (`pytest`).
- Logging configuration and a basic app config system (e.g. YAML/TOML config
  file for camera IDs, SimConnect connection settings, calibration file paths).
- Manual test harness conventions: how later phases' hardware-in-the-loop tests
  are documented and run (since headset/sim hardware can't be fully mocked in
  CI).

**Inputs/Outputs:** N/A (infrastructure only).

**Exit criteria:** `pytest` runs (even with zero/placeholder tests), linter runs
clean, app entry point starts and logs startup/shutdown with no errors.

**Delivered:**
- Python 3.12 + `uv` (`pyproject.toml`, `uv.lock`, `.python-version`); `src/yokemapping`
  package with `yokemapping` console script and `python -m yokemapping`.
- `ruff` (lint + format), `mypy --strict`, `pytest` with a `hardware` marker
  excluded by default.
- Config: frozen `Settings` dataclass with validation; precedence defaults <
  `yokemapping.toml` < `.env` < `YOKEMAPPING_*` environment variables.
- Logging to stderr plus optional rotating file (`log_dir`).
- HIL test conventions in [TESTING.md](TESTING.md).

**Validation:** 31 unit tests pass; `ruff check`, `ruff format --check`, and
`mypy src tests` clean; `uv run yokemapping` logs startup/shutdown and exits 0
(Windows, Python 3.12.8).

---

## Phase 1 — Headset & camera connectivity

**Status:** In progress — 1a (PC side) nearly done; 1b (on-headset cameras) **blocked** (2026-10-29): colour cameras need passthrough visible, and the tracking-camera frames are not reachable (see "Where we left off")

**Checklist — 1a, PC side (SteamVR/OpenVR):**
- [x] OpenVR background session and HMD pose stream (90 Hz, 100% valid on hardware)
- [x] Controller pose streams; selectors `hmd` / `left` / `right` / `serial:<id>`
- [x] Source-agnostic `Pose` / `PoseSample` types and rotation math
- [x] Auto-reconnect, device-event handling, status logging (unit-tested against a fake runtime)
- [x] `yokemapping-pose-monitor` and `yokemapping-steamvr-probe` tools
- [x] Camera-access spike: PC-side `IVRTrackedCamera` exposes no camera
- [ ] Manual run (several minutes): controller off/on and SteamVR restart, all devices return to `ok`

**Checklist — 1b′, tracking (SLAM) cameras (tried 2026-10-29; negative result):**
- [x] Probe tooling: `tools/frame_trackcam_probe.sh` (16-bit aware, no images), `tools/frame_trackcam_grab.sh` (one still, consent only), `tools/run_frame_test.ps1 -Script/-FetchDir`; patches verified against upstream `ptgrab`
- [x] Capture layer reinstalled/enabled on the headset for the probe (still installed; no longer needed)
- [x] T1/T1b (Home, room view off, streaming from PC): the 8 shared 1056×1024 buffers are **empty**; the live 1152×4608 buffers are **decoder output**, not cameras
- [x] One frame grabbed and viewed (garbled decoder data); deleted from headset and PC
- [x] **Result: live tracking-camera frames are not reachable through the compositor layer** (XRService keeps them private)
- [ ] ~~T2/T3 (MSFS runs)~~ — not needed
- [ ] ~~Map buffer pixels to the camera model; yoke detection on tracking-camera frames~~ — blocked by the above
- [x] "2-second look" (S1, owner-approved): the SLAM cameras clearly show the yoke and hands in both left and right; headset dataset deleted, images deleted from the PC
- [ ] Optional: SLAM stereo triangulation tool (fisheye model from `sensorRigCalibration.json`, click landmarks in left and right images, triangulate, combine with the headset pose) and validate against controller touch capture

**Checklist — 1b, on-headset colour camera access (done; kept as reference):**
- [x] Developer Mode on; SSH key login working
- [x] Read-only recon of the headset (OS, toolchain, video nodes, temperatures)
- [x] Inspect the calibration JSONs; identify the camera video nodes (extrinsics only; intrinsics need Valve's recording command)
- [x] Record Valve's calibration dataset; copy the calibration (intrinsics, ~64 mm baseline) to the PC and delete the video from the headset
- [x] Audit spyjacob's capture stage (layer, `ptgrab`, scripts): opt-in, user-scope, local-only; reusable
- [x] Adopt spyjacob's capture stage (layer + `ptgrab`); installed and verified on our unit (sees the 2464² colour stream; decompress works)
- [x] Live test with room view on: both cameras ~60 fps, 1.2 ms copy, CPU +7 °C over 20 s (2026-10-20)
- [x] Test whether the cameras stay active while MSFS is displayed with room view off: **they do not (0 fps)**; with room view on they run at 60 fps (runs A–C)
- [ ] ~~Decide how camera tracking fits flight~~ — shelved: the controller-based approach was chosen instead
- [ ] ~~10-minute thermal run with MSFS and room view on (test D)~~ — shelved (peak so far 85.7 °C in 2 minutes)
- [ ] ~~Check image quality of a downscaled frame (and the ~80° FOV estimate)~~ — shelved
- [ ] ~~On-headset grabber: downscaled frames, with a temperature watchdog~~ — shelved
- [ ] ~~Stream frames to the PC over the LAN and display them~~ — shelved (no longer a Phase 1 exit criterion)

**Goal:** Reliably connect to the Steam Frame via SteamVR and read HMD and
controller poses. (Original goal also included raw passthrough camera frames;
that part is the shelved Phase 1b research track.)

**Scope:**
- Initialize an OpenVR/OpenXR session against the SteamVR runtime.
- Confirm HMD pose stream (position/orientation) at an acceptable, stable rate.
- Enumerate and pull frames from the passthrough cameras via
  `IVRTrackedCamera` (or the current equivalent extension).
- Basic diagnostics: frame rate, latency, dropped-frame handling, and graceful
  handling of headset disconnect/reconnect.
- **Decision:** start by studying/forking
  [spyjacob/steam-frame-hand-tracking](https://github.com/spyjacob/steam-frame-hand-tracking)'s
  Vulkan passthrough-capture layer and OpenVR driver scaffolding (MIT
  licensed) rather than building camera access from scratch, given how
  closely it already matches this phase's needs — while treating it as
  fragile/WIP reference material (per its own README) rather than a stable
  dependency, and keeping our own capture/driver code swappable if it breaks.

**Resolved architecture risk (historical):** spyjacob's README states
that during normal Steam Link streaming, camera images "never leave the
headset" — it reaches them only via a Vulkan layer injected into
`vrcompositor` **on the Frame itself** (built/installed on-device), sending
just ~150-byte pose packets to the PC. It also reports the Frame CPU already
runs at ~70–85 °C while streaming. If PC-side `IVRTrackedCamera` cannot see
Frame camera frames, the "single Windows PC app, no on-headset component"
architecture can't deliver camera frames, and we must choose between:
- **(a)** a two-part design: an on-headset capture component (spyjacob-style)
  that either streams downscaled frames to the PC for inference or runs a
  lightweight detector on-device and sends poses only (heavy models such as
  FoundationPose are unlikely to fit the Frame's thermal budget); or
- **(b)** promoting the controller-mounted fallback (Phase 2 contingency) to
  the primary tracking path, which needs no on-headset component.
  **Outcome (2026-10-21): the owner chose controllers (touch-based
  registration) over cameras.**

First Phase 1 task: a **camera-access spike** — from the PC, enumerate
SteamVR tracked devices and query `IVRTrackedCamera` (`HasCamera`, frame
size, acquire a frame) with the Frame both streamed and (if supported)
tethered — to decide which path applies before further Phase 1/2 work.

**Spike result (2026-10-20): PC-side camera access UNAVAILABLE.** Tool:
`uv run yokemapping-steamvr-probe` (`src/yokemapping/probes/steamvr_camera.py`).
Environment: Windows PC, SteamVR 2.17.10, Steam Frame (OpenVR model
`Deckard MP`, manufacturer `Valve`, tracking system `cv`); connection mode
(streamed vs. tethered) not independently verified. Findings:
- `IVRTrackedCamera::HasCamera` = false for the HMD (the only device present;
  controllers were off).
- The Frame's PC-side driver publishes no camera properties at all:
  `Prop_HasCamera_Bool`, `Prop_NumCameras_Int32`, `Prop_CameraFrameLayout_Int32`
  → `TrackedProp_UnknownProperty`; `Prop_HasCameraComponent_Bool` = false.
- SteamVR's legacy camera setting is already on (`camera.enableCamera: true`
  in `steamvr.vrsettings`), so this isn't a disabled setting.
- Consistent with spyjacob's finding that camera images stay on the headset.

Consequence: the single-PC architecture cannot obtain camera frames through
OpenVR. Re-run the probe after major SteamVR/Steam Frame updates in case
Valve exposes cameras to PC apps.

**Decision update (2026-10-21, owner): use the controllers as the calibration
tool instead of the cameras.** After the live tests showed the cameras run
only while passthrough is visible (0 fps with MSFS and room view off), the
owner chose controller-assisted registration (see the Proposal below) as the
primary approach. Camera work is **shelved, not deleted**: the capture stage
works (60 fps, intrinsics recorded) and the findings below remain valid if we
return to it, for example for a keyed passthrough window or future firmware.

**Decision (2026-10-20, owner, superseded above): identify the yoke with the cameras if
possible.** Camera tracking is therefore the primary goal, which requires a
small component running **on the headset** (Linux/ARM64). The controller path
is kept for two reasons: it is the fallback if cameras prove inadequate, and
it is the **ground-truth source** for validating camera tracking (strap a
controller to the yoke while the camera tracker runs and compare poses).

What spyjacob's findings (README/SYSTEM.md/REPORT.md, 2026-10-11) imply for
that component — all from a third party, to be verified on our headset:
- Reachable cameras: only the **colour passthrough pair** ("Arcturus Camera
  V3", 2464×2464 colour NV12, 60 fps, ~95–100° FOV, factory Kannala-Brandt
  calibration available from the headset's `sensorRigCalibration.json`).
  Our unit has the aftermarket Arcturus colour camera accessory, which is what
  provides this pair; the stock Frame's four mono cameras are not usable by
  apps. SLAM and upper cameras are not reachable live.
- Access is via a passive Vulkan layer in `vrcompositor` (opt-in, with a crash
  guard); no OS-protection bypass. Needs developer/SSH access to the Frame,
  which now works (Developer Mode, `steamos` password, key login). Steps,
  sources, risks, results and rollback:
  [STEAMFRAME_DEV_ACCESS.md](STEAMFRAME_DEV_ACCESS.md). No official camera API
  exists; this route is unsupported and can break on updates.
- **Thermals are the hard limit:** the Frame already idles at ~67–81 °C while
  streaming and rebooted from overheating with two flat-out MediaPipe threads.
  No usable NPU path was found. So heavy models (e.g. FoundationPose) do not
  belong on the headset; the design must keep on-device work light.
- Resulting architecture to evaluate (decide in the Phase 2 spike):
  **(A)** on-headset: grab + downscale/crop frames only, send them to the PC
  over the LAN (~1 ms RTT measured by spyjacob) and run the heavy model on the
  PC GPU; **(B)** on-headset: light detector only, send poses. Prefer (A) for
  accuracy and keeping the headset cool; it requires streaming images, which
  spyjacob's project deliberately avoids (privacy: camera images show the
  room — keep them on the LAN, never saved by default).

**Measured constraint (2026-10-20): the cameras only run while passthrough is
visible.** In MSFS with room view off they deliver 0 fps; with room view on,
60 fps on both cameras. Options for using them in flight (decision pending
with the owner; the first is the provisional recommendation):
1. **Register, then fly:** use passthrough briefly to locate and register the
   yoke in space (and calibrate), then turn room view off and drive the
   virtual yoke from the Moza's own USB axes. Cheap, cool and robust; fits the
   Phase 4 open question below.
2. **Keyed passthrough window:** keep a small passthrough region visible (for
   example around the real yoke, as spyjacob's `steam-frame-chroma-key` does),
   which may keep the cameras live during flight and lets you see the real yoke.
   Untested: needs checking that the cameras stay active with a keyed window,
   plus a heat test.
3. **Full passthrough during flight:** unacceptable for immersion and ran the
   hottest in tests; not recommended.
4. **Find a way to keep the cameras on without passthrough:** unknown whether
   possible; would mean digging into Valve's internal camera service.
   High risk to tracking stability; not recommended without a clear need.

**The target yoke:** Moza **AY210** body with the Moza **MFY** yoke. Phase 2
needs its real dimensions/geometry (3D model, scan, or careful measurements)
for model-based pose estimation. Note this hardware is also a USB HID device,
so its **control axes can be read directly from Windows** (DirectInput/HID);
camera/controller tracking is needed for the yoke's *position in space*, not
for its axis values — see the open question under Phase 4.

**Prerequisites:** Steam Frame + SteamVR on the development PC — confirmed
2026-10-20. MSFS2024 installed — confirmed 2026-10-20. Yoke: Moza AY210 +
MFY — confirmed. Developer/SSH access to the Frame — working (2026-10-20).
Still needed for Phase 2: a controller tip you can touch points with (the
Frame controllers as-is, or a printed tip) and the yoke's dimensions.

**Inputs:** SteamVR runtime + connected/streamed Steam Frame headset.
**Outputs:** Live HMD and controller pose streams (source-agnostic `Pose` /
`PoseSample` types in `src/yokemapping/tracking/pose.py`, produced by
`SteamVRTracker` in `src/yokemapping/tracking/steamvr.py`). Camera frames are
deferred to the on-headset research track (see spike result above).

**Exit criteria (revised after the spike):** App can run for several minutes
while SteamVR is active, continuously streaming HMD and controller poses at
≥ 60 Hz, and recovering cleanly (without restarting the app) when a
controller is powered off/on and when SteamVR is restarted.

**Pose-stream validation (2026-10-20, SteamVR 2.17.10):**
- `uv run yokemapping-pose-monitor --list` found the HMD (`Deckard MP`) and
  both controllers (`Roy_Left` / `Roy_Right`, with serials usable as
  `serial:<id>` selectors).
- 8 s monitor of `hmd`, `left`, `right`: 90 Hz each, 100% valid poses
  (720/720 samples per device); hardware test `-m hardware`: HMD 100% OK over
  5 s.
- Reconnect logic (SteamVR quit/restart, device off/on, role changes, serial
  re-resolution) is covered by unit tests against a fake runtime.
- **Remaining to close Phase 1:** a manual run of several minutes during which
  a controller is powered off and back on and SteamVR is restarted, e.g.
  `uv run yokemapping-pose-monitor --device hmd --device left --device right --seconds 300`,
  confirming each device returns to `ok` without restarting the monitor.

---

## Phase 2 — Yoke registration (controller-assisted)

**Status:** In progress — software built and simulation-tested (2026-10-29); hands-on tests with real controllers and the Moza yoke are next ([TESTING.md](TESTING.md), runs H1–H4)

**Checklist:**
- [x] Decision (2026-10-21): controllers are the calibration tool; camera tracking shelved
- [x] Decision (2026-10-29): camera detection reopened with the tracking cameras; **result negative the same day** (frames not reachable live, Phase 1b′), so the controller approach is primary again
- [x] Target yoke identified: Moza AY210 body with MFY yoke
- [x] Controller pose source in software (`SteamVRTracker`, `serial:<id>` selector), 90 Hz on hardware
- [x] Prior art reviewed (OpenVR-SpaceCalibrator for the alignment maths)
- [x] Registration maths, pure and unit-tested (`src/yokemapping/registration/geometry.py`): total-least-squares line fits, Kabsch (full and yaw-only), pivot tip-offset solve with conditioning checks; tested on synthetic noisy data (NumPy added)
- [x] Yoke frame from landmarks and real-to-virtual alignment (`yoke.py`): frame definition, sanity warnings, residual reporting
- [x] Capture tooling (`capture.py`, `store.py`, `cli.py`): hands-free hold-still capture with beeps/optional speech, pivot, noise and repeatability checks, validated JSON files; `uv run yokemapping-capture` (162 tests incl. a full simulated pivot, capture, align flow)
- [x] Live check: `yokemapping-capture devices` finds the HMD and both controllers; hardware test passes
- [x] H1: measure resting jitter of a controller on the desk (`noise`) — 2026-10-29: right controller, 900 samples, RMS 0.03 mm, worst 0.07 mm (passes; default 2 mm radius is fine)
- [~] H2: pivot calibration of the touch point on a controller (RMS ≤ 3 mm; repeat twice) — 2026-10-29, right controller, bottom point, hand-held: three runs gave RMS 8.4 / 5.2 / 5.5 mm (did not meet 3 mm; a hand-held pivot slides). Tips along the controller 146.8 / 140.3 / 135.0 mm. Workaround: use the mean `--tip-offset-mm 1.9,-7.7,140.7` (error of a few mm should mostly cancel because real and virtual are touched in a similar orientation; H3/H4 will show whether it matters). A clamped divot would give a proper pivot
- [ ] H3: capture the real Moza yoke twice; repeatability RMS ≤ 3–5 mm and reported grip spacing matches a ruler
- [ ] H4: capture the virtual yoke in MSFS (no recenter afterwards) and align; residual RMS ≤ ~15 mm  
- Candidate (not started, owner to decide): locate the real MFY from its faceplate (text, HDG/NAV/ALT/APR/HOME buttons, thumbsticks) by feature matching against a scaled reference photo, using the colour cameras with room view on. Owner measurements so far: top gap between the closest top points ≈ 155 mm; HDG top edge to APR bottom edge = 48.02 mm; faceplate width = 74.67 mm (caliper). Offline feature-matching test on the owner's straight-on photo (888 SIFT keypoints): stays accurate (~0.3 px) down to ~35% of reference size at 30° tilt, fails at ~25% with heavy blur; synthetic only, a real headset frame is the true test. **Real frame test (2026-10-29, `tools/frame_colour_snapshot.sh`):** 8 colour-camera buffers grabbed (6 non-black, 2464² fisheye, rotated 90°, plate ≈ 350 px wide, in both cameras). SIFT + RANSAC against the photo found the real plate in all 6 (30–54 inliers, consistent position between buffers of the same camera, not fooled by the monitor copy). Not yet done: the exact `kb_polar_spline` lens model (spline terms ignored so far), and conversion to tracking space (HMD pose during the grab had ~7 mm std, so a pose captured at the exact frame time would be needed). **Stereo result, first pass (same data, KB lens model without the spline correction, rig frame):** buffers 18-21 = `passthrough_right`, 22-25 = `passthrough_left`, no extra rotation (other combinations give ~70 mm depth, i.e. nonsense). The lens spline terms change results by <1 mm, so they can be ignored. **Reference scale:** the earlier "crop width = 74.67 mm" assumption was wrong; HDG-top to APR-bottom (48.02 mm) is ~221 px in the photo = 4.597 px/mm, and the caliper width gives ~4.54 px/mm (agree within ~1.3 %). **After undistorting matched points before fitting the plate outline (`tools/faceplate_stereo_probe.py --px-per-mm 4.597`):** plate at ~350 mm; flat-plane fit 0.5/1.7/0.6 mm RMS over three pairs, ray miss 0.2-0.4 mm, size within ~1-3 % of the real plate; two same-time pairs agree on the plate centre to ~3 mm (the third pair is from a different moment). **Tooling for the next test (built, selftested, not yet run on the headset):** `tools/faceplate_sweep.py` (owner-run multi-pose grab: records the HMD pose continuously, grabs frames per pose, 8 poses by default, retries if the head moved >3 mm) and `tools/faceplate_handeye.py` (solves the rig->HMD transform from the sweep so the plate lands on the same spot in tracking space from every pose; reports wander, leave-one-pose-out error and the faceplate pose; `--selftest` recovers a synthetic transform to 0.01 deg / 0.2 mm). **Sweep 1 result (8 head poses, 2026-10-29, `data/frames/sweep_20261029-225343`):** HMD pose recording was excellent (<1 mm / <0.3 deg movement per grab). **Key finding: only buffers 18, 19 (right) and 22, 23 (left) are live; 20/24 never update (stale frame from an earlier moment) and 21/25 are black.** The grab log's CHANGED/same flags identify fresh buffers; averaging in the stale pair had poisoned the first hand-eye solves (31 mm wander). With fresh buffers only, 4 poses solved to 2.3 mm wander; plate matching failed in the other 4 (dim, noisy, steeply oblique), but **predicting the plate's image position from the first solve and refining there (rectify-and-rematch) recovered all 8 poses**. **Result: headset<-rig transform rotvec [1.357, -2.977, 0.873] translation [57.2, -19.1, -41.0] mm; faceplate seen from 8 head poses lands within 3.7 mm RMS of the same spot in tracking space (leave-one-pose-out 1.9-12.3 mm, mean 6.0 mm); faceplate centre in tracking space (0.331, 1.711, -0.245) m.** No published MFY dimensions exist online (research: MOZA pages are JS-rendered, no manual/CAD/forum numbers found); the metric faceplate model is the owner's straight-on photo scaled by 4.597 px/mm (from the 48.02 mm HDG-APR span; cross-checked by the 74.67 mm width and by the stereo scale 1.01-1.05). Better lighting should improve matching and the numbers; thermal focal-length model and per-frame timing are untried refinements. **Sweep 2, better lighting (`data/frames/sweep_20261029-234848`):** 7 of 8 poses matched directly (the 8th via prediction); plane fits 0.3-0.7 mm RMS and ray miss 0.06-0.26 mm (about half of sweep 1). Headset<-rig transform rotvec [-1.117, 2.565, -0.755], translation [53.4, -23.5, -40.7] mm (agrees with sweep 1: [57, -19, -41] mm). Plate wander between poses **3.3 mm RMS**, leave-one-pose-out **mean 4.8 mm** (2.4-8.1 mm; the worst poses are those where the head moved most during the grab). Faceplate centre in tracking space (0.527, 1.669, -0.221) m, about 20 cm from sweep 1's position because the tracking origin/yoke position differs between sessions, so the registration must be redone per SteamVR session. Remaining error is likely systematic (focal-length/thermal model, grab timing), not matching. **Stereo-screenshot picking vs. the overlay (2026-10-30):** clicking a "bottom centre" in screenshots depends on guessing which point is the centre. Replaced as the main route by `yokemapping-overlay`: a world-space ghost (cross-hair, 50 mm ticks, faceplate outline) sits at the real yoke's tracked pose and the owner nudges it onto the virtual yoke's centre from inside the headset using a dashboard panel (controller laser); the saved offset (yoke frame: x right, y up, z toward the pilot, plus yaw/pitch/roll) is the real-to-virtual registration. Verified: overlays are created and torn down cleanly against live SteamVR; not yet verified: laser clicks (the y-axis flip is a guess, `--no-flip-y` switches it). Headset multifunction button and controller buttons produce no events a PC overlay app can see (listened for 25 s while pressing them), so the laser is the only input. The screenshot picker remains available for measuring the virtual yoke objectively. Steam's F12 does not apply to the Store version, but OpenVR `IVRScreenshots::TakeStereoScreenshot` through the compositor does work (`tools/vr_screenshot_probe.py`): a side-by-side stereo PNG (2148x2135 per eye) plus the HMD pose. OpenVR's per-eye tangents do NOT describe what MSFS rendered (30-110 px errors), so the pixel->ray mapping is calibrated from the known head motion between shots (`tools/vr_screenshot_calibrate.py`, 5 shots, 10 pairs): both eyes f = 947 px (square pixels), cx = 1100/1104, cy = 586/548 (optical centre well above the image centre), median Sampson error 3.7 px (pose-timing limited; the probe now uses the pose around the request instead of a 4 s mean). **Validation: the same cockpit features triangulated from different shots agree to a median of 7.5 mm (90th percentile 12.9 mm) in tracking space, over 3153 features.** Per-feature stereo depth error is ~4 mm per pixel at 0.6 m, so averaging several shots and sub-pixel clicks should reach ~3-5 mm per landmark. **Still to do:** the landmark picking tool (click the same virtual-yoke landmarks in both eye images of each shot, triangulate to tracking space, average across shots), then align with the real faceplate pose in the same SteamVR session.
- [ ] Decide how to apply the offset (MSFS VR cockpit offset vs. SteamVR play-space shift) and test it
- [ ] Optional: SteamVR overlay wireframe of the real yoke for visual verification
- [ ] Optional: SLAM stereo-click registration, compared against this method
- [ ] Exit criteria met

**Goal:** Determine the pose of the physical yoke (origin and axes) in the
SteamVR tracking space by touching landmarks with a tracked controller, and
register it to the virtual yoke in the sim, without cameras.

**Scope:**
- Tip-offset (pivot) calibration of the controller.
- Averaged point capture (tip held still ~1 s), with outlier rejection.
- Fit the yoke's roll axis (shaft) and grip line by total least squares, build
  the third axis by cross product, re-orthogonalise, report residuals in mm.
- Repeat for the virtual yoke as it appears in the headset; solve the rigid
  transform between the two frames (Kabsch), reporting the alignment error.
- Persist captures so Phase 6 can store them as profiles.
- Details and caveats: see the Proposal below.

**Inputs:** Controller pose stream (Phase 1a); the owner touching landmarks on
the real yoke and on the virtual yoke.
**Outputs:** Yoke frame (origin, axes, residuals) in SteamVR space; real-to-
virtual transform with its error; saved capture files.

**Exit criteria:** Repeated captures of the Moza yoke agree within an agreed
tolerance (to be set after the first measurements; target a few mm to ~1 cm),
the real-to-virtual alignment error is measured and reported, and the process
is repeatable from the CLI in a few minutes.

### Camera-based yoke detection (former Phase 2 scope; on hold)

On hold: the tracking-camera route was tried on 2026-10-29 and live frames are
not reachable (Phase 1b′); the colour cameras need passthrough visible. If it
is ever resumed, mono fisheye frames at 1056×1024 favour classical
model-based fitting of the known Moza geometry over colour-oriented models such
as FoundationPose.

- **Goal:** detect the yoke in the camera feed and
  estimate its 6DoF pose without fiducial markers.
- Evaluate and select a markerless detection/pose approach (e.g. an object
  detector plus geometric pose refinement, or a vision-capable ML model) —
  document the tradeoffs considered.
- Camera-to-headset/world coordinate calibration so a detected yoke pose can be
  expressed in the same space as HMD pose.
- Architect the detection pipeline so it can be extended to other cockpit
  controls (throttle, trim wheel, etc.) in a later phase, rather than being
  yoke-specific by construction.
- Start with a constrained setup (known yoke make/model, controlled lighting)
  before attempting to generalize — markerless detection accuracy/latency
  should be measured and understood before relying on it for real-time control
  input.
- **Decision:** evaluate [NVlabs/FoundationPose](https://github.com/NVlabs/FoundationPose)
  as the primary candidate pose-estimation model (zero-shot 6DoF pose of a
  known rigid object given a 3D model/scan of the target yoke) before
  building a bespoke detector, checking its GPU/latency budget against the
  Steam Frame/PC hardware; fall back to a lighter model family (Isaac ROS
  DOPE, MediaPipe Objectron) if it proves too heavy.

**Inputs (shelved):** Camera frames from Phase 1; known/approximate yoke geometry for
validation.
**Outputs (shelved):** Real-time 6DoF pose (and confidence/latency metrics) of the
physical yoke.

**Exit criteria (shelved):** Pose estimate stays within an acceptable error margin and
update rate across a range of head positions/lighting for a fixed test rig, with
measured detection latency and failure modes documented.

### Reference: controller-mounted tracking (former contingency plan)

Originally the fallback if camera detection failed; now an alternative to the
touch-registration proposal below (strap a controller to the yoke instead of
touching points on it). The notes are kept because the maths is shared.

Markerless camera-based pose estimation carries real risk (lighting
sensitivity, occlusion, motion blur, model accuracy limits). If, after
reasonable tuning, the camera-based approach cannot reliably meet this
phase's exit criteria, fall back to **tracking the yoke with a SteamVR motion
controller instead of the cameras**:

- Physically mount/strap one of the Steam Frame's motion controllers (or
  another SteamVR-tracked device, if available) directly to the physical
  yoke.
- Use the controller's native SteamVR 6DoF pose (already surfaced by
  `steamvr-integration` in Phase 1 — the same inside-out tracking system used
  for the HMD) as the yoke pose source, bypassing camera-based detection
  entirely.
- This trades off controller availability (the mounted controller is no
  longer free for other in-app interaction) and requires simple mounting
  hardware, but is far more robust and lower-latency than vision-based
  detection, since it reuses SteamVR's already-validated tracking pipeline
  rather than a new, unproven CV/ML model.
- **Architecture requirement:** Phase 2's output must be an implementation-
  agnostic "yoke pose stream" interface. Phases 4–6 consume that stream and
  must not care whether it originated from camera-based CV or a
  controller-based fallback, so switching (or offering both, user-selectable)
  requires no changes downstream.
- Decision point: attempt the camera-based approach first; adopt the
  controller fallback for a given setup if exit-criteria accuracy/latency
  isn't achievable, or offer it as a user-selectable mode in the app config.
- **Decision:** use [pushrax/OpenVR-SpaceCalibrator](https://github.com/pushrax/OpenVR-SpaceCalibrator)'s
  rigid-transform/space-alignment math as the reference for reconciling the
  mounted controller's tracked space with the yoke's physical reference frame,
  rather than deriving that alignment math from scratch.

### Proposal — controller-assisted registration (owner idea, 2026-10-21)

Instead of strapping a controller to the yoke, **touch points on it with a
controller and fit its axes**, then do the same on the virtual yoke and
align the two. Needs no cameras, no heat, and only the PC-side pose stream we
already have.

1. **Pivot calibration (once):** the controller's tracked origin is not its
   tip. Pivot the controller about a fixed point (or use a printed tip) and
   solve the tip offset by least squares, so touched points are accurate.
2. **Capture the real yoke:** touch and record points (each by a PC key press
   with the tip held still for ~1 s, averaged): along the shaft/column
   (roll axis) and at the left and right grips (lateral axis). Fit each line
   by total least squares (SVD), build the third axis by cross product, and
   re-orthogonalise. This gives the yoke's origin and axes (its x/y/z frame).
   Report the fit residuals in mm so a bad capture is rejected.
3. **Capture the virtual yoke:** with MSFS running, touch the same landmarks
   where the virtual yoke appears in the headset. Both point sets live in
   the same SteamVR tracking space.
4. **Align:** solve the rigid transform between the two frames (Kabsch/
   Procrustes; or yaw + translation only if tilt is already matched). Use it to
   place a SteamVR overlay wireframe of the real yoke, show the alignment
   error, and give the offset needed to line the virtual yoke up (for example
   via MSFS's VR cockpit offset, or a play-space shift; applying the shift is
   the least understood step and needs checking).
5. **Live motion** then comes from the Moza's USB axes (or a mounted
   controller) rather than from tracking, which also removes any need for the
   cameras during flight.

Caveats to test: controller accuracy at arm's length (expect mm to ~1 cm),
tip-offset error, and that the virtual yoke may move or differ per aircraft
(this is also the Phase 5 center/scale capture). The math (line/plane fits,
Kabsch, pivot solve) is pure and unit-testable with synthetic noisy data, so
it can be built and validated before any hardware step.

---

## Phase 3 — MSFS2024 connectivity

**Status:** Not started

**Checklist:**
- [x] MSFS 2024 installed on the development PC
- [x] SimConnect libraries reviewed (Python-SimConnect, MobiFlight)
- [ ] Choose the SimConnect library
- [ ] SimConnect session to a running MSFS 2024, with reconnect
- [ ] Subscribe to yoke / aileron / elevator variables at a usable rate
- [ ] Confirm sign, range and units against manual input in the sim
- [ ] Exit criteria met

**Goal:** Reliably connect to MSFS2024 via SimConnect and read the virtual
yoke's axis data.

**Scope:**
- Establish a SimConnect session against a running MSFS2024 instance.
- Subscribe to relevant simulation variables (yoke/aileron/elevator input and
  position, at minimum) at a usable update rate.
- Confirm round-trip read of control values during manual yoke input in-sim;
  prepare (but don't yet require) the ability to write control values for
  Phase 4.
- Handle MSFS2024 start/stop/reconnect gracefully.

**Inputs:** Running MSFS2024 instance with SimConnect enabled.
**Outputs:** Live stream of virtual yoke axis values.

**Exit criteria:** App reads virtual yoke axis values in real time while
manually flying in MSFS2024, with correct sign/range and no dropped
connection over an extended session.

---

## Phase 4 — Real-to-virtual yoke mapping

**Status:** Not started

**Checklist:**
- [x] Open question recorded: the Moza already reports its axes over USB
- [ ] Owner decides what the tracked pose should add (see the open question below)
- [ ] Mapping math (pose to pitch/roll axes)
- [ ] Hotkey / soft-button controls: engage, recenter, scale
- [ ] Fail-safe behaviour for lost tracking decided and documented
- [ ] Exit criteria met

**Goal:** Map the tracked real yoke pose (Phase 2) onto the virtual yoke's axes
(Phase 3), with user-facing controls for engaging the mapping.

**Scope:**
- Mapping math: convert real yoke pitch/roll pose to virtual yoke
  pull/push and rotate axes.
- Hotkey and/or soft-button UX: engage/disengage mapping, recenter, adjust
  sensitivity/scale on the fly.
- Safety considerations: fail-safe behavior if tracking is lost mid-flight
  (e.g. hold last value vs. release to neutral — decide and document).

**Open question (owner to decide before Phase 4):** the Moza AY210/MFY
already reports its pitch/roll axes over USB, and MSFS can read them directly
(the sim yoke then animates by itself). So what should the tracked pose add?
Candidates: (1) place/align the virtual yoke and hands with the real yoke in
space (visual registration); (2) drive a virtual yoke on aircraft or setups
where the physical axes aren't bound; (3) the generalized mapping in Phase 5
for mismatched yokes. The answer decides whether Phase 4 writes axis values via
SimConnect at all, or only needs pose for rendering/registration.

**Inputs:** Phase 2 yoke frame and real-to-virtual transform; the Moza's USB
axes and/or a live pose source (a controller mounted on the yoke), depending on
the open question above; virtual yoke axis read/write (Phase 3).
**Outputs:** Real yoke movement reliably drives the virtual yoke in MSFS2024.

**Exit criteria:** A test pilot can fly a basic maneuver (e.g. straight and
level, then a coordinated turn) using only the real yoke, with mapping
toggled live via hotkey/soft button.

---

## Phase 5 — Generalized control mapping

**Status:** Not started

**Checklist:**
- [ ] Capture the real yoke's center and motion scale
- [ ] Capture the pilot's/HMD position relative to the yoke
- [ ] Generalized center/scale mapping for mismatched controls
- [ ] Extend detection to other controls where useful
- [ ] Exit criteria met

**Goal:** Support aircraft whose virtual yoke/control type doesn't match the
real yoke's geometry, by mapping based on relative position and scale instead
of exact matched geometry.

**Scope:**
- Capture the real yoke's center position and motion scale/range.
- Capture the pilot's/HMD's position relative to the yoke center.
- Derive a generalized mapping: real yoke displacement (relative to its own
  center and scale) maps proportionally to virtual control displacement,
  regardless of exact control type.
- Extend detection (building on Phase 2's architecture) to other real-world
  controls where useful.

**Inputs:** Real yoke center/scale calibration; HMD/pilot position; virtual
control metadata for the current aircraft.
**Outputs:** Usable control mapping for aircraft without a matching yoke type.

**Exit criteria:** Mapping produces controllable, proportionate input on at
least one aircraft with a differently-shaped/sized yoke or control, validated
against the Phase 4 exit criteria maneuver.

---

## Phase 6 — Calibration & profile persistence

**Status:** Not started

**Checklist:**
- [ ] Profile format (center/scale, calibration, per-aircraft curves, sensitivity)
- [ ] Save / load with a sensible default when no profile exists
- [ ] Basic profile management (list, select, create, delete)
- [ ] Exit criteria met

**Goal:** Avoid recalibrating every session by persisting calibration data.

**Scope:**
- Define a profile format capturing: yoke center/scale, camera-to-world
  calibration, per-aircraft (or per-yoke-type) mapping curves, and
  sensitivity settings.
- Save/load profiles, with a sensible default/fallback when no profile exists
  yet for the current aircraft.
- Basic profile management UX (list, select, create-from-current-calibration,
  delete).

**Inputs:** Calibration data produced during Phases 2, 4, and 5.
**Outputs:** Persisted profiles on disk, loaded automatically per aircraft
where possible.

**Exit criteria:** App restarts, loads an existing profile for a previously
calibrated aircraft, and reproduces working control mapping without
re-running manual calibration.

---

## Prior art & related work

Researched prior to further implementation (web + GitHub, deep pass). No
existing project combines markerless CV yoke tracking + VR passthrough +
SimConnect end-to-end — that specific combination appears to be open ground —
but strong, reusable prior art exists for every sub-piece. Revisit this list
before starting each phase below; some of these may become direct
dependencies rather than being rebuilt from scratch. Note (2026-10-21): the
camera-related entries (spyjacob's repos, FoundationPose) now belong to the
shelved camera track; for the current plan the most relevant entries are
OpenVR-SpaceCalibrator (alignment maths), Python-SimConnect/MobiFlight
(Phase 3) and fsmapper (axis mapping).

### Very relevant — closest prior art / likely dependencies

- **[spyjacob/steam-frame-hand-tracking](https://github.com/spyjacob/steam-frame-hand-tracking)**
  (MIT) — runs on-device on Steam Frame, taps the passthrough cameras via a
  Vulkan capture layer, runs MediaPipe hand detection at 60 Hz, and streams
  pose over UDP to a Windows **OpenVR driver** that exposes the tracked hand
  as a Frame controller to SteamVR, with pick-up/set-down handoff logic vs. a
  physical controller. **Closest known architectural match to Phase 1/2's
  primary (camera-based) tracking path** — study the capture layer, OpenVR
  driver scaffolding, and its `REPORT.md`/`LATENCY_REPORT_V2.md` perf findings
  before building our own. Very new (created Oct 2026) and still evolving.
- **[spyjacob/steam-frame-chroma-key](https://github.com/spyjacob/steam-frame-chroma-key)**
  (MIT) — chroma-key passthrough window on Steam Frame (paint a key colour in
  the SteamVR background, replaced live with the real passthrough feed;
  ~0.3ms GPU cost). Directly relevant if we ever want a "see my real yoke"
  passthrough window alongside pose-mapping. Explicitly fragile against
  SteamVR internals per its own README — track for breakage, don't treat as
  stable.
- **[odwdinc/Python-SimConnect](https://github.com/odwdinc/Python-SimConnect)**
  (311★) — ctypes wrapper around `SimConnect.dll`; `AircraftRequests`/
  `AircraftEvents` for reading/writing sim vars and triggering events,
  including MobiFlight custom events. Natural starting point for Phase 3's
  SimConnect dependency.
- **[MobiFlight/MobiFlight-Connector](https://github.com/MobiFlight/MobiFlight-Connector)**
  (359★) + **MobiFlight-WASM-Module** — the dominant open-source home-cockpit
  SimConnect bridge; good reference for registering/throttling custom axis
  events reliably.
- **[pushrax/OpenVR-SpaceCalibrator](https://github.com/pushrax/OpenVR-SpaceCalibrator)**
  (783★, many active forks, e.g. `hyblocker/OpenVR-SpaceCalibrator` with
  continuous calibration) — computes the rigid transform between two SteamVR
  tracking spaces/devices. This is exactly the coordinate-alignment problem
  Phase 2's **controller-mounted fallback** faces (reconciling a controller
  strapped to the yoke with the SteamVR play space) — study its transform
  math directly rather than reinventing it.
- **[NVlabs/FoundationPose](https://github.com/NVlabs/FoundationPose)**
  (3,646★, CVPR 2024 Highlight) — zero-shot 6DoF pose estimation/tracking of
  a known rigid object from a 3D model/reference images, no per-object
  retraining. Current SOTA candidate model for Phase 2's markerless yoke pose
  estimation; needs a 3D model/scan of the target yoke and a GPU inference
  budget (evaluate against Steam Frame/PC compute constraints noted in
  spyjacob's `REPORT.md`).

### Partially relevant — useful reference, not a direct dependency

- **[opentrack/opentrack](https://github.com/opentrack/opentrack)** (5,218★) —
  long-running webcam/marker-based head-tracking for flight sims
  (TrackIR-style). Not yoke tracking, but the most mature open precedent for
  the "camera → smoothing/filtering → pose → sim protocol" pipeline shape;
  worth studying its filtering and output-protocol code.
- **[opiopan/fsmapper](https://github.com/opiopan/fsmapper)** (42★) — MSFS/DCS
  axis remapping (linearity, polarity, hysteresis) and virtual panel windows;
  conceptual precedent for Phase 5's center/scale-based generalized mapping.
- **[mbucchia/_ARCHIVE_XR_APILAYER_NOVENDOR_hand_to_controller](https://github.com/mbucchia/_ARCHIVE_XR_APILAYER_NOVENDOR_hand_to_controller)**
  — OpenXR API layer translating hand tracking into synthetic VR-controller
  input; same "alternative input → standard controller abstraction" shape as
  our real-yoke → virtual-yoke mapping. Same author also maintains widely-used
  OpenXR runtime-layer tooling (`OpenXR-Toolkit`, `PimaxXR`,
  `VirtualDesktop-OpenXR`) worth knowing for Steam Frame OpenXR compatibility.
- **NVIDIA-ISAAC-ROS/isaac_ros_pose_estimation**, **MediaPipe Objectron** —
  alternate/lighter-weight 6DoF pose model families if FoundationPose proves
  too heavy for the target hardware.
- **DCS-Skunkworks/dcs-bios** — DCS World's MobiFlight equivalent; same
  bridge pattern, different sim.

### Commercial landscape (context only, not open source)

FSUIPC (closed-source axis/telemetry bridge), NaturalPoint TrackIR (commercial
camera+marker head tracking — the historical commercial analog of our camera
tracking concept), Varjo (VR headsets known in the flight-sim community for
passthrough-based cockpit mixed reality, claim not independently re-verified
this pass), and home-cockpit builders (Flight Deck Solutions, Virtual Fly,
Simxperience, OpenA3XX) plus yoke hardware vendors (Honeycomb, WinWing,
VIRPIL, RealSimGear, Thrustmaster, CH Products). None of these appear to do
markerless CV yoke tracking fed into a VR passthrough/SimConnect pipeline —
reinforcing that this project's specific combination is not an existing
product category today.

### Gaps / follow-ups

- No dedicated open-source project for "VR tracker mounted on a yoke/joystick"
  was found — the fallback plan's core technique is well known informally in
  the simpit/DCS VR community but undocumented in a public repo.
- No yoke-specific pose-estimation model or academic paper was found;
  FoundationPose/DOPE/Objectron are the closest generic building blocks and
  would need a custom 3D model/scan of the target yoke.
- General web/forum search (Google/Bing/DuckDuckGo/Reddit/AVSIM/flightsim.to)
  was largely inaccessible to automated fetching this pass — a manual
  browser-based follow-up (r/hoggit "trackers on hardware" threads, AVSIM VR
  cockpit forum threads, Varjo's own passthrough marketing copy) is
  recommended to firm up the claims flagged above as unverified.
- `spyjacob`'s two repos are extremely new (created 2026-10-11) and
  explicitly marked fragile/WIP — valuable study material, risky as a hard
  dependency; monitor for breakage and consider reaching out given the direct
  overlap.
