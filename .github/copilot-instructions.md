# YokeMapping — Project Guidelines

## Architecture

Single Windows PC application that bridges a real-world flight yoke (Moza
AY210 + MFY) to the virtual yoke in Microsoft Flight Simulator 2024:

- **Headset & controllers**: Valve Steam Frame via SteamVR's OpenVR runtime
  (`IVRSystem` for HMD/controller poses, 90 Hz, in use).
- **Flight sim**: Microsoft Flight Simulator 2024 via the official SimConnect
  SDK (Windows-only; requires MSFS2024 running locally on the same PC).
  Not started (Phase 3).
- **Registration (primary approach)**: controllers are the calibration tool:
  touch landmarks on the real and virtual yoke, fit axes, align frames
  (Phase 2). Live yoke motion is expected from the Moza's USB axes.

Primary language: Python.

> **Direction (2026-10-29):** the owner tried the headset's **tracking (SLAM)
> cameras** for yoke detection; the result was negative the same day. The
> compositor's shared camera-sized buffers (8 × 1056×1024 NV12) are empty, the
> live 1152×4608 buffers are the video decoder's output for the Steam Link
> stream, and the real tracking frames are private to XRService (not
> reachable without risky or blocked methods). The colour cameras run only while
> passthrough is visible. So **controller-assisted registration** (touch points
> on the real and virtual yoke, fit axes, align) is the primary approach again;
> camera work is on hold. Do not resume camera work unless asked (an optional
> offline experiment with Valve's dataset recorder exists). See "Where we left
> off" in [docs/ROADMAP.md](../docs/ROADMAP.md) and
> [docs/STEAMFRAME_DEV_ACCESS.md](../docs/STEAMFRAME_DEV_ACCESS.md).
>
> Known: the Steam Frame exposes no camera to PC-side OpenVR apps; the owner's
> unit has the aftermarket Arcturus colour camera accessory.
> `uv run yokemapping-steamvr-probe` re-checks PC-side camera access after
> SteamVR updates.

## Phase discipline

This project is built in phases, each with its own test/exit criteria — see
[docs/ROADMAP.md](../docs/ROADMAP.md) for the phased outline, checklists and
the current status. Do not implement later-phase functionality
ahead of validating the current phase's exit criteria, and update the
roadmap's status markers and checklists as work lands (including the "Where we
left off" section).

## Specialized agents

Domain-specific custom agents are available in `.github/agents/` for
SteamVR/OpenVR integration, SimConnect integration, calibration/mapping
(registration maths, current priority), and vision (shelved). Prefer
delegating to the matching specialist agent for deep work in its domain.

## Working with the headset

Headset access, test runners and rollback steps are in
[docs/STEAMFRAME_DEV_ACCESS.md](../docs/STEAMFRAME_DEV_ACCESS.md) and
[docs/TESTING.md](../docs/TESTING.md). In PowerShell, pipe scripts to `ssh`
instead of using `<`; never put the headset password in files or chat, and keep
unit-specific data (calibration, test runs) under the gitignored `data/` folder.
Ask the owner before changing anything on the headset or recording camera video.

## Build and Test

Python 3.12, managed with `uv` (`pyproject.toml` + `uv.lock`). Source lives in
`src/yokemapping/`, tests in `tests/`.

```powershell
uv sync                    # install/update the .venv
uv run pytest              # unit tests (hardware tests excluded by default)
uv run pytest -m hardware  # hardware-in-the-loop tests (needs Steam Frame/SteamVR/MSFS2024)
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy src tests      # strict type checking
```

All four checks (pytest, ruff check, ruff format --check, mypy) must pass
before committing. Add dependencies with `uv add <pkg>` (or `uv add --dev`),
never by hand-editing the lockfile. See [docs/TESTING.md](../docs/TESTING.md)
for hardware-test conventions.

## Conventions

- Settings live in the frozen `Settings` dataclass in `src/yokemapping/config.py`;
  add new settings there (with validation in `__post_init__`) and document them
  in `yokemapping.example.toml` and `.env.example`. Env vars use the
  `YOKEMAPPING_` prefix.
- Use `logging.getLogger(__name__)`; never configure logging outside
  `logging_setup.py`.
- Keep hardware access (OpenVR, SimConnect, cameras) behind small interfaces so
  logic built on top can be unit-tested with fakes.
- Registration code lives in `src/yokemapping/registration/`: `geometry.py`
  (pure maths), `yoke.py` (yoke frame, alignment), `capture.py` (hold-still
  capture, pivot, noise), `store.py` (validated JSON), `cli.py`
  (`yokemapping-capture`). Units are meters in SteamVR tracking space; keep the
  maths pure and test it with synthetic noisy data (see `tests/test_registration_*.py`).
  Tests that drive the CLI must pass a recording announcer (never the console one,
  which beeps).
