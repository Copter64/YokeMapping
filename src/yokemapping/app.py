"""Application entry point and lifecycle."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from yokemapping import __version__
from yokemapping.config import LOG_LEVELS, ConfigError, Settings, load_settings, log_level_number
from yokemapping.logging_setup import configure_logging

log = logging.getLogger(__name__)


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yokemapping",
        description="Map a real flight yoke onto the virtual yoke in MSFS2024 via Steam Frame.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        type=Path,
        help="path to a TOML config file (default: ./yokemapping.toml if present)",
    )
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        type=str.upper,
        help="override the configured log level",
    )
    return parser.parse_args(argv)


def run(settings: Settings) -> None:
    """Run the application's subsystems until they finish or are interrupted.

    Subsystems (headset, vision, SimConnect, mapping) are added phase by phase; see
    docs/ROADMAP.md.
    """
    log.info("No subsystems are enabled yet (Phase 0 scaffolding).")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        settings = load_settings(args.config)
        if args.log_level:
            settings = replace(settings, log_level=args.log_level)
    except ConfigError as exc:
        print(f"yokemapping: configuration error: {exc}", file=sys.stderr)
        return 2

    configure_logging(log_level_number(settings), settings.log_dir)
    log.info("YokeMapping %s starting", __version__)
    log.debug("Settings: %s", settings)

    exit_code = 0
    try:
        run(settings)
    except KeyboardInterrupt:
        log.info("Interrupted by user")
    except Exception:
        log.exception("Unhandled error")
        exit_code = 1
    finally:
        log.info("YokeMapping shut down (exit code %d)", exit_code)
    return exit_code
