# Steam Frame developer access and camera path

> **Status (2026-10-29): the tracking-camera route was tried and is closed for
> now.** The developer-access sections (SSH, recon, risks) remain current. The
> colour cameras run at 60 fps only while passthrough is visible; the tracking
> cameras' frames are private to XRService and not reachable through the
> capture layer (results below). The capture layer is installed and enabled on
> the headset (reinstalled for the probe; removal command at the end).

Researched 2026-10-20. Sources are Valve's Steamworks docs unless marked
*third-party* (spyjacob's repos, one author, unverified by Valve).

## Hardware context: the camera accessory

The stock Steam Frame has four monochrome tracking/SLAM cameras that apps
cannot use. The **colour passthrough pair** exists only because this unit has
the aftermarket **Arcturus colour camera accessory** (two colour sensors on
the expansion port). Evidence on our unit: `colorPassthroughCalibrationHistory.json`
with device serial `AV6246U21`. Consequences:

- Everything in this project that depends on camera frames assumes the
  accessory is attached; without it there is no passthrough image to read.
- spyjacob's "Arcturus Camera V3" passthrough cameras (2464×2464 colour NV12,
  60 fps, ~95–100° FOV) are therefore the same accessory, which is why his
  findings should transfer to our unit.
- The SLAM cameras (mono, 1056×1024) and upper cameras are not reachable by
  his method. We have only the colour pair to work with.
- The accessory's field of view, mounting and thermal contribution are
  properties of this third-party hardware. Re-validate if it is swapped or
  firmware-updated.

## Why there is no "connect" option

Developer Mode does not add a connect button or pairing screen on the headset.
It only enables **SSH, ADB and RDP** on the headset; you connect **from the PC**.
The step people miss is **Set User Password**, which is a separate button.

Sources: <https://partner.steamgames.com/doc/steamhardware/steamframe/setup>,
<https://partner.steamgames.com/doc/steamhardware/steamframe/debugging>.

## Procedure

1. On the headset: **Steam Settings > System > Enable Developer Mode**.
2. In the left menu, open the new **Developer** section, scroll to the bottom of
   that page and click **Set User Password** (virtual keyboard). This is the
   password for the Linux user `steamos`.
3. Reboot the headset (not in Valve's docs; try it if the ports stay closed).
4. Find the headset: the default hostname is `frame`. On this PC it resolves to
   `<FRAME_IP>` (the real address lives only in the local `~/.ssh/config`
   `Host frame` entry, never in the repo) (checked 2026-10-20: ping works; ports 22, 2222, 3389, 5555,
   5037, 8022, 9000 were all **closed** before the password was set).
5. From PowerShell on the PC (OpenSSH is already installed here):

   ```powershell
   ssh steamos@frame          # or: ssh steamos@<FRAME_IP>
   ```

   Other official routes: Remote Desktop (mstsc) to `frame` as `steamos` for a
   full Linux desktop, and ADB (`adb connect frame`) for the Android layer.
   WinSCP is Valve's suggestion for copying files.
6. Optional, passwordless login. Run these **on the PC**, not in the headset shell:

   ```powershell
   ssh-keygen -t ed25519 -f $HOME\.ssh\id_ed25519_frame -N ""
   type $HOME\.ssh\id_ed25519_frame.pub | ssh steamos@<FRAME_IP> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
   ssh -i $HOME\.ssh\id_ed25519_frame steamos@<FRAME_IP> echo ok
   ```

   Pitfalls hit on 2026-10-20: (a) `-N '""'` in PowerShell sets a passphrase
   of literal `""`, so the server accepts the key but login still fails; use
   `-N ""` (fix an existing key with `ssh-keygen -p -f <key> -P '""' -N ""`);
   (b) the `type ... | ssh` line only works on the PC; run in the headset's own
   shell it appends nothing (check with `wc -l ~/.ssh/authorized_keys`).

7. Verify the password step worked: `Test-NetConnection frame -Port 22` should
   report `TcpTestSucceeded : True`.

If the ports stay closed after steps 1–3: make sure the PC and headset are on
the same subnet with no client isolation (guest Wi-Fi/mesh), use the headset's
IP shown under Quick Settings or Steam Settings > Internet, and re-check that
Developer Mode is still on.

## What is and is not officially supported

- There is **no documented API** for third-party apps to read the Frame's
  passthrough cameras. Valve's docs cover only offline tracking recordings
  (`start_tracking_recording` via `DriverDebugRequest`), meant for bug reports.
- Our probe agrees: PC-side `IVRTrackedCamera` exposes no camera.
- *Third-party:* spyjacob's only working method is an unofficial Vulkan layer
  loaded into `vrcompositor` that duplicates the camera buffers' file
  descriptors, installed under `$HOME` only, behind an opt-in flag, with
  uninstall scripts. It depends on undocumented internals and can break on any
  SteamOS/SteamVR update. He rejected opening `/dev/video*` directly and
  stealing handles from `XRService`.
- The headset's root filesystem is read-only. `sudo steamos-readonly disable`
  enables `pacman`, but Valve warns OS updates overwrite such changes. His
  build needs only `g++`, `glslc`, `spirv-tools`, `python3` and `git`, which
  ship on the stock image.

## Risks

- Heat: the Frame idles at ~67–81 °C while streaming and rebooted once under
  heavy on-device load (third-party). Keep on-headset work light and watch
  temperatures.
- Updates can break the layer. Valve recommends the Beta update channel;
  **do not switch channels** unless needed, and record the OS/SteamVR version
  any result was measured on.
- Warranty implications of Developer Mode are not stated anywhere found.
- Privacy: camera frames show your room. Keep them on the LAN, off disk by
  default.
- Everything here is reversible: the third-party layer ships `uninstall.sh`
  and `rollback.sh`; a SteamOS update also wipes system-level changes.

## Reconnaissance result (2026-10-20, read-only, `tools/frame_recon.sh`)

Key-based SSH works: `ssh frame` (local `~/.ssh/config` entry holding the
address and key; equivalent to `ssh -i $HOME\.ssh\id_ed25519_frame steamos@<FRAME_IP>`).

- **OS:** SteamOS (Arch-based), kernel `6.18.0-g83913f38113c` built 2026-10-06,
  aarch64, hostname `frame`. SteamVR runs from `/opt/steamvr`
  (`vrcompositor` and `XRService` both running; `steamvr` user service active).
- **Resources:** 15.7 GB RAM (~10 GB available), `/home` 929 GB (892 GB free),
  `/` 10 GB (5.9 GB free). User `steamos` is in the `video`, `render`, `input`
  and `perf` groups.
- **Build tools present on the stock image:** `g++`, `gcc`, `glslc`, `cmake`,
  `python3` 3.12.3, `pip3`, `git`, `curl`. No need to unlock the filesystem.
- **Cameras:** `/dev/video0..21` plus `video22/23` (decoder/encoder links) and
  `video99`, owned `root:video` (mode 660; our user is in `video`).
  spyjacob avoided opening these directly because `XRService` owns them.
  Which node is which camera is not yet identified.
- **Calibration:** `~/.config/openvr/config/cv/xrservice/` holds
  `sensorRigCalibrationHistoryForExpSLAM.json` (290 B, extrinsics only) and
  `colorPassthroughCalibrationHistory.json` (21 KB: front-cam-from-passthrough
  and passthrough stereo **extrinsics** only, as spyjacob also found; **no lens
  intrinsics/distortion**). The intrinsics come from Valve's debug command,
  see "Getting the camera intrinsics" below.
- **Video nodes (identified via sysfs, without opening them):** `video0–16` are
  Qualcomm `msm_vfe0–4` camera ISP outputs, `video17–21` are `msm_metadata*`,
  `video22/23` are the `qcom-iris` decoder/encoder (used by `vrlink`
  streaming), `video99` is a SteamVR virtual camera. `fuser` shows `XRService`
  holding `video0/3/6/7/9/13`; do **not** open them (contention with SteamVR
  tracking). The compositor shares XRService's camera dma-bufs instead.
- **Vulkan layers:** system layers only (fossilize, Steam overlay, MangoHud,
  gamescope WSI, Mesa); no third-party layer installed.
- **Not installed:** nothing from spyjacob (`~/handtrack`, `~/.config/mrhand`
  absent), so the headset is stock.
- **Temperatures (SteamVR running, idle):** CPU cores 59–71 °C, GPU 55–59 °C,
  camera sensors ~56 °C; load average ~2.6; uptime 37 min.

## Getting the camera intrinsics (official, offline) — done 2026-10-20

Valve's debug driver command records a dataset that includes
`sensorRigCalibration.json` with full factory intrinsics and extrinsics for
every camera. Run on our unit (about 10 s, owner-approved), using the full
path because `vrcmd` is not on the PATH in non-interactive shells:

```bash
V=/opt/steamvr/bin/linuxarm64/vrcmd
$V --cmdhmd "start_tracking_recording 1 1 1"    # response: ok
sleep 10
$V --cmdhmd "stop_tracking_recording"           # response: ok
```

The dataset (`~/.config/openvr/config/cv/xrservice/datasets/Deckard_<date>.dataset/`)
contained camera video and snapshots of the room, so only three small JSON
files were copied to the PC (`data/frame/`, gitignored: unit-specific) and the
whole dataset folder was then **deleted from the headset** (folder verified
empty). Copied: `sensorRigCalibration.json`,
`..._sensorsFromHead.json`, `..._device_config.json`.

**Result** (`sensorRigCalibration.json`, rig v2, factory calibration, Kannala-Brandt
model with `fx fy cx cy k1..k4` per camera):

| camera | sensor | size | fx (px) |
|---|---|---|---|
| `passthrough_left` / `passthrough_right` | Arcturus Camera V3 (colour) | 2464×2464 | ~1473 / ~1468 |
| `slam_left` / `slam_right` | Sunny AW01D04-12085 (mono) | 1056×1024 | ~327 |
| `upper_left` / `upper_right` | Sunny F00V16AA (mono) | 640×480 | ~230 |

- Passthrough stereo baseline from the extrinsics: **63.9 mm** (matches
  spyjacob). Approximate field of view on the full frame, from a pinhole
  estimate: ~80° (his 95–100° figure likely differs in definition; verify).
- Each passthrough camera has a `cropRegion` (x offset 550 for left, 250 for
  right) and a `focalLengthThermalModel` (slope 0.325): **the focal length
  drifts with temperature**, which matters for pose accuracy and should be
  modelled or monitored.
- Intrinsics apply to the raw buffer pixels (spyjacob: no extra crop offset
  beyond `cropRegion`; verify when we have frames). A 640² downscale is a
  factor of ~3.85, so scale `fx fy cx cy` accordingly.

**S1 result (2026-10-29, owner approved, ~2 s recording, headset worn, looking
at the yoke):** the recorder created `Deckard_<date>.dataset` (76 MB). Its
`BeginningSnapshot`/`EndSnapshot` folders each held: `slam_left.png`,
`slam_right.png`, `upper_left.png`, `upper_right.png` (plus `-controller`
LED-exposure variants), `passthrough_left/right.png` (2464² colour, ~12.6 MB
each), `leftDisplaySnapshot.png`, `status_SLAMConsole.png`,
`cameraMetadata.json`, `shortTermRecorder_liveTrackedStates.ocsv` (headset
poses) and `shortTermRecorder_slamEvents.txt`. The script copied only the SLAM
and upper images, the console image and the metadata (20 files); the dataset
was then deleted from the headset and the images from the PC. Viewed: the SLAM
scene frames show the yoke, the owner's hands and the desk in both cameras, in
dim but usable light; the LED-exposure frames are almost black.
Tool: `tools/frame_slam_snapshot.sh` via
`run_frame_test.ps1 -Script frame_slam_snapshot.sh -FetchDir /dev/shm/ptsnap`
(consent-gated, deletes only the dataset it created).

## Audit of spyjacob's capture layer (2026-10-20)

Read: `capture/handcam_layer.cpp`, `capture/ptgrab.cpp`, `capture/build.sh`,
`capture/install.sh`, `capture/uninstall.sh`, `FINDINGS.md`. Not yet read:
the tracker and `headset/*.sh` (not needed for the capture stage).

- Loads only into `vrcompositor`, and only while
  `~/.config/mrhand/enable-capture` exists; disables itself if the compositor
  starts 3 times within 120 s (crash guard, state in `~/.config/mrhand/`).
- When the compositor imports XRService's camera dma-bufs it `dup()`s the
  file descriptor first, records the image format/extent/DRM modifier, and
  serves `{metadata + fd}` over a private Unix socket
  (`$XDG_RUNTIME_DIR/mrhand/cam.sock`, same-uid peers only). It records no
  commands, touches no queues, and has no network code. It logs to
  `/tmp/mrhandcam-<pid>.log`.
- Installed under `~/.local/share/mrhand` and
  `~/.local/share/vulkan/implicit_layer.d/mrhand.json` (user scope;
  `DISABLE_MRHAND=1` disables it). `install.sh` also appends to
  `~/handtrack/CHANGES.log`, so create that directory first.
- The passthrough images are 2464×2464 NV12 with the **UBWC** compression
  modifier (`0x0500000000000001`), so they cannot simply be `mmap`ed: they
  need a Vulkan import on the GPU to decompress, which is what his `ptgrab`
  does. `ptgrab.cpp` (read 2026-10-20): connects only to the layer's local
  socket, imports the buffers into its own Vulkan device, submits on its own
  **lowest-priority** queue (never competing with the compositor), downscales
  the **luma (grayscale) plane only** to 640×640 per camera, and publishes to
  `/dev/shm/mrhand-pt`. No network code, no process spawning.
- `build.sh` is two `g++` commands (no root, no package installs);
  `uninstall.sh` removes the manifest, the enable flag and the library.
  Both `install.sh` and `uninstall.sh` append to `~/handtrack/CHANGES.log`.
- Implication: reuse his layer and `ptgrab` as the capture stage, and write
  our own consumer that sends frames to the PC instead of running his hand
  tracker. Grayscale 640² frames are likely enough for the vision stage.

## Tracking cameras (tried 2026-10-29; closed for now)

Owner decision: use the headset's **tracking cameras** (not the colour
accessory) to identify the yoke and its position. Why: SteamVR must run them
continuously to track the headset, so they may stay live in flight with
passthrough off.

**What the calibration says** (`data/frame/sensorRigCalibration.json`, our unit):

| camera | sensor | image | fx (px) | notes |
|---|---|---|---|---|
| `slam_left` / `slam_right` | Sunny AW01D04-12085 (mono) | 1056×1024 | ~327 | `cropRegion.x` 112, ~180° fisheye, low/outward, ~55° apart (spyjacob); `focalLengthThermalModel` slope 0.0985 |
| `upper_left` / `upper_right` | Sunny F00V16AA (mono) | 640×480 | ~230 | ~150° (spyjacob); `parent` Cam1 / Cam0 |

Spyjacob (unverified here): SLAM cameras run at 60 Hz **alternating scene and
controller-LED exposures** (so only every other frame shows the scene), and he
rated them "not reachable live". He also saw two XRService→compositor image
sets: 8 × 2464² (colour) and 8 × 1152×1024 NV12 ("SLAM-sized, content
unverified").

**What our own layer log showed** (2026-10-20): the compositor imports,
besides the 2464² colour images, 8 images of **1056×1024** (NV12, linear) and
16–48 images of **1152×4608** (a 10-bit 2-plane format). The 1152×4608 set was
first guessed to be stacked camera images; the 2026-10-29 probe showed it is
the video decoder's output (see Results below).

**Why `ptgrab` could not show it:** it hard-codes a 2464×2464 filter. Our probe
(`tools/frame_trackcam_probe.sh`) builds a private copy in `/tmp` with the
filter set from environment variables (three one-line changes, verified
against upstream commit `8852ec9`), runs `identify` (per-buffer brightness; the
PGM dump is skipped by pointing it at a non-existent directory) and `stream`
(frames per second per half of the buffer list), for 1056×1024 and 1152×4608,
then deletes everything. It saves no images and changes nothing else.

**Results (2026-10-29, owner wearing the headset in SteamVR Home, room view off,
headset streaming from the PC):**

1. **Layer state:** the 2026-10-21 uninstall had worked (empty `~/.config/mrhand`,
   no manifest). We reinstalled and enabled it for the probe
   (`install.sh`, flag file, `systemctl --user restart steamvr`). It loaded,
   crash guard clear, `cam.sock` present. **It is still installed.**
2. **`1056×1024` NV12, linear (modifier 0), 8 buffers:** all luma means **0.0**,
   never changed, 0 fps. These are the "SLAM-sized" shared buffers; they exist
   but are empty (spyjacob: "stay empty", probably for a monochrome-passthrough
   mode that the colour accessory replaces).
3. **`1152×4608`, 10-bit 2-plane 4:2:0, UBWC, 16 buffers:** non-zero and changing
   between reads, with quarters 1–2 changing continuously and quarters 3–4
   static. **Not cameras:** the headset's `vrlink` was connected to the PC and
   `vrlinkrunthread` held the hardware decoder `/dev/video22`; this is the
   decoder's output format. One frame was grabbed (owner-approved by default),
   viewed once, and found to be garbled tiled data. It was deleted from both
   the headset and the PC.
4. **Where the real tracking frames are:** XRService keeps 32 × 1152×1024 NV12
   (and other sets) private (spyjacob's FINDINGS); `/dev/video0/3/6/7/9/13` are
   held by `XRServiceLoopTh`. Not exposed to the compositor, hence not to our
   layer.

**Corrections to what this document said earlier:** the speculation that
`1152×4608` might be "four stacked cameras" is wrong, and T2/T3 (MSFS runs) were
not needed once T1 showed the shared set is empty and the live set is video.

**What would still reach the tracking cameras** (none done, none recommended
without a clear need):

| Route | What it gives | Cost/risk |
|---|---|---|
| Valve's dataset recorder (`vrcmd --cmdhmd "start_tracking_recording 1 1 1"`) | Per spyjacob's test (our 10 s run on 2026-10-20 listed the same file types: `.occ`, `snapshots/`, pose `.ocsv` files): an Occipital `.occ` file with **one SLAM camera's H.264 video at 30 fps** (scene frames, with controller-LED exposure frames interleaved, 60 Hz sensor) plus ~1 kHz IMU; headset pose logs; and **`snapshots/BeginSnapshot` and `EndSnapshot`: one full-resolution PNG of every camera** (both SLAM, both upper, both passthrough). **Offline only** | Supported debug feature; records the room (consent needed); ~370 MB per 55 s, far less for a few seconds. A **2-second recording is a cheap way to see what the SLAM cameras see** (is the yoke in view?). A one-shot registration needs more work than the controller touch method |
| XRService IPC (`libArcturusPerception`) | raw frames, in principle | Single-session protocol owned by driver_cv; a second client may disrupt tracking |
| Intercepting XRService's private buffers | live frames | `ptrace_scope=1` blocks `pidfd_getfd`; would mean bypassing OS protections: out of bounds |

**Tools kept:** `tools/frame_trackcam_probe.sh` (inventory + liveness, 16-bit
aware, no images), `tools/frame_trackcam_grab.sh` (one still, only with
consent), and `tools/run_frame_test.ps1 -Script … -FetchDir …` (copies results
or stills back, always deletes the headset copy). The patched `ptgrab` is
verified against upstream `8852ec9` with Git's `sed` before each use.

**Risks and honest caveats (if the camera track is ever resumed):** fisheye
distortion at ~1.5 mm/pixel at 0.5 m; alternating scene/LED exposures halve the
usable frame rate; the cameras are mounted low and outward so the yoke may be
partly out of view; the focal length drifts with temperature; and every route
above depends on undocumented internals that a SteamVR or SteamOS update can
break.

## Left installed on the headset (state at 2026-10-29)

**Reinstalled today:** the capture layer is **installed and enabled** again
(`~/.config/mrhand/enable-capture`, `~/.local/share/vulkan/implicit_layer.d/mrhand.json`),
and SteamVR on the headset was restarted to load it. With the tracking-camera
route closed it has no use, so remove it when convenient:

```powershell
ssh -i $HOME\.ssh\id_ed25519_frame steamos@<FRAME_IP> "bash ~/handtrack/src/capture/uninstall.sh && systemctl --user restart steamvr"
```

## Left installed on the headset (earlier note, 2026-10-21)

The capture layer is **still installed and enabled** (`~/.config/mrhand/enable-capture`
exists; `~/handtrack/src` holds the source). It is passive, but with camera work
shelved you may prefer to remove it. To remove it:

```bash
ssh -i $HOME\.ssh\id_ed25519_frame steamos@<FRAME_IP>
bash ~/handtrack/src/capture/uninstall.sh && systemctl --user restart steamvr
```

Developer Mode, SSH (key `id_ed25519_frame`) and the `steamos` password also
remain enabled.

## Capture stage installed on our headset (2026-10-20)

State of the headset after this step (all user-scope; nothing under `/usr` or `/opt`):

- Source: `~/handtrack/src` = `spyjacob/steam-frame-hand-tracking` at commit
  `8852ec9bdd4f81fb5a359e5bf88ed25f0e6fc89e` (2026-10-16). Built with
  `capture/build.sh` (two `g++` commands). Binaries in `capture/build/`.
- Layer installed: `~/.local/share/mrhand/libVkLayer_mrhand.so` and
  `~/.local/share/vulkan/implicit_layer.d/mrhand.json`.
- Layer **enabled**: `~/.config/mrhand/enable-capture` exists and SteamVR was
  restarted (`systemctl --user restart steamvr`, 23:33). Hand tracking itself
  is **not** installed or running.

Results:

- The layer loaded into `vrcompositor` (log `/tmp/mrhandcam-<pid>.log`), the crash
  guard did not trip, and `cam.sock` was created (mode 600).
- It sees **16 NV12 images of 2464×2464** with the Qualcomm UBWC modifier
  (`0x500000000000001`, 2 planes): the colour passthrough stream.
- `ptgrab identify` imported all 8 buffers and decompressed their luma planes
  on the GPU in ~10 ms per pass (39 ms first pass). Test frames were written
  to `/tmp` only and deleted.
- `ptgrab stream 30 640` with the headset idle: 0.0 fps (cameras not
  refreshing the buffers). **With "room view" (passthrough) toggled on in the
  headset, it delivers both cameras at ~60 fps** (55.0, 59.9, 60.1 fps on
  successive seconds), full-frame copy ~1.2 ms. Measured 2026-10-20 over 20 s
  with `tools/frame_capture_test.sh`; CPU hottest-zone temperatures rose from
  61.7 °C to 68.7 °C (cpu2-bottom) while streaming. 20 s is too short to know
  the steady-state temperature; a longer run is still needed.
- **Staged live tests (2026-10-20, `tools/run_frame_test.ps1`, MSFS in VR):**

  | Run | Setup | Result |
  |---|---|---|
  | B | MSFS running, room view **off**, 60 s | **0.0 fps, 11/11 samples dead**; hottest zone fell to ~61 °C |
  | C | MSFS running, room view **on**, 120 s | cam0/cam1 **60.0 fps avg, min 59.7**, 23/23 live; hottest 71 → **85.7 °C** peak at 105 s (ended 74.9 °C) |
  | A | idle, room view **on**, 120 s (run after C) | **60.0 fps**, 23/23 live; hottest 70–84.5 °C, fluctuating, not climbing |

  Raw outputs are in `data/test_runs/` (gitignored).
- **Conclusion on activation: the cameras only run while the passthrough/room
  view is being shown.** With a full-screen VR app and room view off they are
  completely inactive (0 fps). So continuous camera tracking during flight is
  not possible as-is; it needs passthrough visible somewhere (see the
  options in the roadmap's Phase 1b notes).
- **Conclusion on heat:** the hottest zone peaked at 85.7 °C, below spyjacob's
  93 °C watchdog and the 95–97 °C zone where he saw a reboot, but it was still
  trending up during run C. Steady state is unknown; the capture alone added
  only ~3–7 °C in earlier tests, so most heat is MSFS streaming plus
  passthrough. A 10-minute run (test D) is staged.

Rollback (reverts everything above):

```bash
bash ~/handtrack/src/capture/uninstall.sh   # removes manifest, flag and library
rm -rf ~/handtrack ~/.config/mrhand          # optional: remove source and state
systemctl --user restart steamvr             # unload the layer from the compositor
```

## Next steps

1. **Owner:** repeat the live test in a realistic situation: MSFS running in
   VR with room view **off**, then with room view on, and a longer run
   (`SECS=120`), to learn whether the cameras stay active during flight and
   the steady-state temperature. In PowerShell (no `<` redirect):

   ```powershell
   (Get-Content tools\frame_capture_test.sh -Raw) -replace "`r","" | ssh -i $HOME\.ssh\id_ed25519_frame -o BatchMode=yes steamos@<FRAME_IP> 'SECS=120 bash -s'
   ```

   (A trailing `$'\r': command not found` line is harmless.)
2. Write our own on-headset consumer: read `/dev/shm/mrhand-pt` (layout to be
   confirmed from the rest of `ptgrab.cpp`), downscale/crop, and send frames to
   the PC over the LAN, with a temperature watchdog.
3. Build the PC-side receiver and show the frames (Phase 1b exit).
