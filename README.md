# YokeMapping

Maps a real-world flight yoke's movement onto the virtual yoke in **Microsoft
Flight Simulator 2024 (MSFS2024)**, as seen through a **Valve Steam Frame** VR
headset.

## Status

Phase 0 (scaffolding) complete. Phase 1a (PC-side headset/controller pose
streaming, 90 Hz) done apart from one manual run. Camera route tried and on
hold: the colour cameras only run while passthrough is visible, and on
2026-10-29 the tracking cameras turned out not to be reachable live. So
**controller-assisted registration** (touch points on the yoke, fit axes) is the
primary approach; next is building its maths. See
[`docs/ROADMAP.md`](docs/ROADMAP.md) ("Where we left off").

## Architecture

Single Windows PC application (Python):

- **Headset & controllers** — SteamVR's OpenVR runtime (HMD and controller
  poses). Camera frames are not exposed to PC apps.
- **Flight sim** — Microsoft's SimConnect SDK (yoke/aileron/elevator axis
  data); Phase 3, not started.
- **Registration** — touch landmarks on the real yoke (Moza AY210 + MFY) and on
  the virtual yoke with a controller, fit the yoke's axes, and align the two
  frames; live motion expected from the Moza's USB axes.
- **Camera research (on hold)** — colour cameras run at 60 fps but only while
  passthrough is visible; the tracking cameras' frames are not reachable live
  (tried 2026-10-29). Results and tools are kept in
  `docs/STEAMFRAME_DEV_ACCESS.md`.

## Key docs

- [`docs/ROADMAP.md`](docs/ROADMAP.md) — phased plan, per-phase checklists,
  current status and next steps, prior art.
- [`docs/TESTING.md`](docs/TESTING.md) — automated and hardware-in-the-loop
  test conventions, plus the staged live tests.
- [`docs/STEAMFRAME_DEV_ACCESS.md`](docs/STEAMFRAME_DEV_ACCESS.md) — Steam Frame
  developer access (SSH), camera research results, what is installed on the
  headset and how to remove it.
- [`.github/copilot-instructions.md`](.github/copilot-instructions.md) —
  project-wide conventions for AI coding agents working in this repo.
- [`.github/agents/`](.github/agents) — specialist custom agents (SteamVR,
  SimConnect, vision [on hold], calibration/mapping,
  project lead).
- [`tools/`](tools) — scripts for the headset (`frame_recon.sh`,
  `frame_capture_test.sh`, `frame_trackcam_probe.sh`) and `run_frame_test.ps1`,
  the one-command runner (`-Script` picks the test) that saves results under
  the gitignored `data/` folder.

## Getting started

Requires Windows, Python 3.12, and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync                  # create .venv and install dependencies
uv run yokemapping       # run the app (or: uv run python -m yokemapping)
uv run pytest            # run the tests
```

Configuration precedence (lowest to highest): built-in defaults <
`yokemapping.toml` < `.env` < real environment variables.

- Copy [`yokemapping.example.toml`](yokemapping.example.toml) to
  `yokemapping.toml` and/or [`.env.example`](.env.example) to `.env` to
  configure your machine (both copies are gitignored).
- Environment variables use the `YOKEMAPPING_` prefix, e.g.
  `YOKEMAPPING_LOG_LEVEL=DEBUG`.
- `uv run yokemapping --help` lists command-line options (`--config`,
  `--log-level`, `--version`).

## Diagnostics

- `uv run yokemapping-steamvr-probe` — checks whether PC-side OpenVR can read
  the headset's passthrough camera frames (start SteamVR with the headset
  connected first). Exit code 0 = available, 1 = unavailable,
  2 = inconclusive; `--json` and `--save-frame PATH.ppm` are available.
  As of SteamVR 2.17.10 the Steam Frame reports **unavailable**.
- `uv run yokemapping-capture --help` — controller-assisted registration of the
  real yoke to the virtual yoke (Phase 2): `devices`, `noise`, `pivot`,
  `capture --target real|virtual`, `align`, `show`. Hands-free (hold the
  controller point still ~1 s; beeps confirm). Procedure and pass criteria:
  [`docs/TESTING.md`](docs/TESTING.md), runs H1–H4. Captures are saved under
  the gitignored `data/captures/`.
- `uv run yokemapping-overlay` — SteamVR overlay for lining the real yoke up with the virtual one
  from inside the headset (Phase 2). Open the SteamVR dashboard and pick "YokeMapping"; point
  a controller laser at the buttons and pull the trigger to nudge the "ghost" (the real yoke's
  tracked position, shown in the world) onto the virtual yoke's centre, then Save
  (`data/captures/virtual_offset.json`). It reads the real yoke's pose from the latest camera
  sweep (`data/frames/sweep_*/handeye.json`, or `--real-pose`). Use `--no-flip-y` if the
  buttons react one row off. The headset's multifunction button and the controller buttons are
  not visible to PC apps, so the laser is the only input.
- `uv run yokemapping-pose-monitor --list` — list tracked devices (HMD,
  controllers) with their roles and serials.
- `uv run yokemapping-pose-monitor --device hmd --device right --seconds 60` —
  stream poses and print per-device rate, status, position, and
  yaw/pitch/roll each second, then a summary. Devices: `hmd`, `left`,
  `right`, or `serial:<id>`.
