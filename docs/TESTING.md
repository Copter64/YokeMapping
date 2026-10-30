# Testing conventions

## Automated tests (no hardware)

```powershell
uv run pytest            # unit tests; hardware tests are skipped by default
uv run ruff check .      # lint
uv run ruff format .     # format (use --check in verification)
uv run mypy src tests    # strict type checking
```

Every module should be unit-testable without a headset, SteamVR, or MSFS2024
running. Keep hardware access behind a thin interface (e.g. a pose source, a
camera frame source, a SimConnect client) so the logic on top of it can be
tested with fakes.

Tests run in an isolated temp directory with all `YOKEMAPPING_*` environment
variables cleared (see `tests/conftest.py`), so a developer's local `.env` or
`yokemapping.toml` never affects results.

## Hardware-in-the-loop (HIL) tests

Tests that need real hardware or software (Steam Frame + SteamVR, MSFS2024)
are marked with `@pytest.mark.hardware` and are excluded from the default run.
Run them explicitly on a machine with the required setup:

```powershell
uv run pytest -m hardware
```

Each hardware test's docstring must state:

1. **Prerequisites** — what must be running/connected (e.g. "SteamVR running,
   Steam Frame connected and awake", "MSFS2024 loaded into a flight with a
   yoke-equipped aircraft").
2. **Procedure** — any manual action required during the test (e.g. "move the
   yoke through full roll travel when prompted").
3. **Pass criteria** — the measurable result tied to the phase's exit criteria
   in [ROADMAP.md](ROADMAP.md) (e.g. pose rate, latency, value ranges).

Tests should skip (not fail) with a clear reason when a prerequisite isn't
available, so a missing headset is distinguishable from a real regression.

## Phase 1b′ tracking-camera probe (done 2026-10-29; negative result; headset required)

Runner: `tools\run_frame_test.ps1 -Script frame_trackcam_probe.sh`. The probe
reports the layer state first and stops with a clear `RESULT:` line if the layer
is missing, disabled, or not loaded (exit codes 3–8). It saves **no frames**.
`frame_trackcam_grab.sh` (with `-FetchDir /dev/shm/ptframes`) saves ONE still and
must only be run with the owner's consent; the runner copies it to
`data\frames\` (gitignored) and always deletes the headset copy.
Wake the headset first (wear it or press the power button, wait ~30 s);
`ping frame` should answer, otherwise pass `-HeadsetHost <ip>`.

**Result:** run T1 (Home, room view off, streaming from the PC) showed the 8
shared 1056×1024 buffers empty and the live 1152×4608 buffers to be video
decoder output, so T2 and T3 below were **not needed**. Kept as reference:

| Run | Setup | Command | Question answered |
|---|---|---|---|
| T1 (done) | Headset worn, SteamVR Home, **room view off** | `.\tools\run_frame_test.ps1 -Label T1-home-roomview-off -Script frame_trackcam_probe.sh -Seconds 8 -DelaySeconds 5` | Do camera-sized buffers exist and update? |
| T2 (skipped) | MSFS running in VR, **room view off** | same with `-Label T2-msfs-roomview-off -DelaySeconds 20` | Live during flight? |
| T3 (skipped) | MSFS running in VR, **room view on** | same with `-Label T3-msfs-roomview-on -DelaySeconds 20` | Tied to passthrough? |

Read the probe's output: the "images the compositor imported" table (counts by
size), per-buffer `mean` and `bands` values (bands are horizontal quarters),
and the `samples=… live=… half0/half1 … fps` lines. `live=0` or `no fps lines`
means the buffers do not update. For 16-bit (10-bit) buffers `PTBPP=2` makes the
probe read them correctly; without it the sizes and offsets are wrong.

If the probe says the layer is not installed or enabled (exit 3 or 4), reinstall
(this restarts SteamVR on the headset and interrupts any VR session):

```powershell
ssh -i $HOME\.ssh\id_ed25519_frame steamos@frame "bash ~/handtrack/src/capture/install.sh && touch ~/.config/mrhand/enable-capture && systemctl --user restart steamvr"
```

## Staged live tests: Phase 1b colour camera capture (done; headset required)

Runner: `tools\run_frame_test.ps1` (copies `tools/frame_capture_test.sh` to the
headset, waits `-DelaySeconds` so you can get into position, runs for
`-Seconds`, cleans up, saves to `data\test_runs\<time>_<label>.txt`, gitignored).
Start SteamVR streaming to the PC first. After each run, tell Copilot
"results saved" and it reads the files.

| Run | Setup | Command | Question answered |
|---|---|---|---|
| A | Headset worn, SteamVR Home or dashboard, **room view on** | `.\tools\run_frame_test.ps1 -Label A-roomview-on-idle -Seconds 120` | Steady-state fps and temperature |
| B | MSFS (or any VR app) running, **room view off** | `.\tools\run_frame_test.ps1 -Label B-msfs-roomview-off -Seconds 60 -DelaySeconds 20` | Do the cameras run while an app is displayed? |
| C | MSFS running, **room view on** | `.\tools\run_frame_test.ps1 -Label C-msfs-roomview-on -Seconds 120 -DelaySeconds 20` | fps and heat under a real load |

Interpreting results: `live` and `cam0/cam1 avg` are the key lines; `0.0 fps`
means the cameras were not active. A hottest-zone temperature that keeps
rising past ~85 °C means we must throttle the capture.

**Results so far (2026-10-20):** B = 0.0 fps (cameras off with room view off),
C = 60.0 fps with a peak of 85.7 °C in 2 minutes, A = 60.0 fps. Details in
[STEAMFRAME_DEV_ACCESS.md](STEAMFRAME_DEV_ACCESS.md).

**Next staged run (thermal) — shelved with the camera track (2026-10-21):**

| Run | Setup | Command |
|---|---|---|
| D | MSFS running in VR, **room view on**, flying or hovering normally | `.\tools\run_frame_test.ps1 -Label D-msfs-roomview-on-10min -Seconds 600 -DelaySeconds 30` |

Read it for: does the hottest zone level off (good) or keep climbing past
~90 °C (stop the run with Ctrl+C and take the headset off the head to cool).

## Phase 2 hands-on tests: controller registration (owner needed)

Tool: `uv run yokemapping-capture` (see `--help`). Results go to
`data\captures\` (gitignored). Start SteamVR streaming to the PC with both
controllers on. `capture` is **step by step** by default and uses the **MFY layout** (butterfly
yoke): `top_left` / `top_right` (the closest-together points across the top, about
155 mm apart), `bottom_center`, `left_grip`, `right_grip`. `--layout column` selects
the older shaft-and-grips layout. Each step is printed
(and beeps/speaks with `--speak`), you touch the landmark with the controller in
your right hand, hold it still, then press **Space or Enter** in the terminal
window with your other hand to capture it (the tip is averaged for 0.6 s and
rejected if it moved). **B** goes back one step, **Q** quits. The terminal
window must have keyboard focus; you run this command yourself in your own
terminal. `--confirm still` switches to the hands-free mode (hold still ~1 s,
one beep = captured, two low beeps = rejected, three rising = finished).
(Reading the controller trigger from a PC app was tried and is not available:
SteamVR gives background apps no button state or events, and the action-input
API stayed inactive.)

Choose **one hard point on the controller** to touch things with (for example the
front rim of the ring) and always use the same point. The pivot step measures where
that point is relative to the controller.

| Run | Setup | Command | Pass criteria |
|---|---|---|---|
| H1 | Right controller lying still on the desk, headset on or off | `uv run yokemapping-capture noise --device right --seconds 10` | Worst jitter ≤ ~2–3 mm; note the suggested `--radius-mm` |
| H2 | A small fixed divot (a dimple in a block, a drilled hole, a rubber foot) and the chosen controller point | `uv run yokemapping-capture pivot --device right` and rotate the controller about that point in all directions for 10 s. Run it **twice** | Fit RMS ≤ 3 mm and the two tip offsets agree within ~2 mm |
| H3 | Headset on, sitting at the yoke, yoke centred and still | `uv run yokemapping-capture capture --target real --device right --out data\captures\real_a.json`, then again with `--out data\captures\real_b.json`, then `uv run yokemapping-capture align data\captures\real_a.json data\captures\real_b.json` | Repeatability RMS ≤ 3–5 mm; **measure the grip spacing with a ruler** and compare it with the tool's "grip spacing" (within ~5 mm) |
| H4 | MSFS running in VR in an aircraft with a yoke, yoke centred, final head position, **no recenter afterwards**; same SteamVR session as H3 | `uv run yokemapping-capture capture --target virtual --device right`, then `uv run yokemapping-capture align data\captures\real_a.json data\captures\virtual_<time>.json` (add `--yaw-only` if both yokes are level) | Residual RMS ≤ ~15 mm; note which landmark has the largest residual |

Landmark order (touch in this order, all with the same controller point):
`shaft_1` (shaft/column near the base), `shaft_2` (middle), `shaft_3` (near the
hub, closest to you), `left_grip` and `right_grip` (the centre of each grip, as
you see them seated). The virtual yoke gets the same five points where it
appears in the headset.

After each run, tell Copilot "results saved"; it will read the printed summaries
you paste or the files in `data\captures`.

## Recording phase validation

When a phase's exit criteria are validated, record the date, hardware/software
versions (SteamVR, Steam Frame OS, MSFS2024 build), and measured results in
that phase's section of [ROADMAP.md](ROADMAP.md), then mark its status `Done`.
