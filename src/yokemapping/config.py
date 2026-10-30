"""Application settings.

Precedence (lowest to highest): built-in defaults < TOML config file < `.env` file
< process environment variables. Environment variables use the ``YOKEMAPPING_``
prefix followed by the upper-cased setting name, e.g. ``YOKEMAPPING_LOG_LEVEL``.
"""

from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

ENV_PREFIX = "YOKEMAPPING_"
DEFAULT_CONFIG_FILE = Path("yokemapping.toml")
DEFAULT_DOTENV_FILE = Path(".env")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(ValueError):
    """Raised when configuration is missing, malformed, or out of range."""


@dataclass(frozen=True)
class Settings:
    log_level: str = "INFO"
    log_dir: Path | None = None
    simconnect_host: str = "localhost"
    simconnect_port: int = 500
    steamvr_camera_index: int = 0
    calibration_profile_dir: Path = Path("calibration_profiles")

    def __post_init__(self) -> None:
        if self.log_level not in LOG_LEVELS:
            raise ConfigError(f"log_level must be one of {LOG_LEVELS}, got {self.log_level!r}")
        if not 1 <= self.simconnect_port <= 65535:
            raise ConfigError(f"simconnect_port must be 1-65535, got {self.simconnect_port}")
        if self.steamvr_camera_index < 0:
            raise ConfigError(f"steamvr_camera_index must be >= 0, got {self.steamvr_camera_index}")


# With `from __future__ import annotations`, field types are their source strings.
_FIELD_TYPES: dict[str, str] = {f.name: str(f.type) for f in fields(Settings)}


def _coerce(name: str, value: Any) -> Any:
    """Convert a raw TOML/env value to the type declared on ``Settings``."""
    declared = _FIELD_TYPES[name]
    try:
        if declared == "int":
            if isinstance(value, bool):
                raise TypeError("booleans are not integers")
            return int(value)
        if declared == "str":
            if not isinstance(value, str):
                raise TypeError(f"expected a string, got {type(value).__name__}")
            return value.upper() if name == "log_level" else value
        if declared in ("Path", "Path | None"):
            if not isinstance(value, str):
                raise TypeError(f"expected a path string, got {type(value).__name__}")
            if declared == "Path | None" and value == "":
                return None
            return Path(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"invalid value for {name!r}: {value!r} ({exc})") from exc
    raise AssertionError(f"unhandled settings type {declared!r} for {name!r}")


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"could not parse config file {path}: {exc}") from exc
    unknown = sorted(set(data) - set(_FIELD_TYPES))
    if unknown:
        raise ConfigError(f"unknown setting(s) in {path}: {', '.join(unknown)}")
    return data


def _read_env(env: Mapping[str, str | None]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name in _FIELD_TYPES:
        raw = env.get(ENV_PREFIX + name.upper())
        if raw is not None:
            values[name] = raw
    return values


def load_settings(
    config_path: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    dotenv_path: Path | None = DEFAULT_DOTENV_FILE,
) -> Settings:
    """Build ``Settings`` from defaults, an optional TOML file, ``.env``, and the environment.

    ``config_path`` must exist if given explicitly; otherwise ``yokemapping.toml`` in the
    current directory is used when present.
    """
    raw: dict[str, Any] = {}

    if config_path is not None:
        if not config_path.is_file():
            raise ConfigError(f"config file not found: {config_path}")
        raw.update(_read_toml(config_path))
    elif DEFAULT_CONFIG_FILE.is_file():
        raw.update(_read_toml(DEFAULT_CONFIG_FILE))

    if dotenv_path is not None and dotenv_path.is_file():
        raw.update(_read_env(dotenv_values(dotenv_path)))

    raw.update(_read_env(os.environ if environ is None else environ))

    return Settings(**{name: _coerce(name, value) for name, value in raw.items()})


def log_level_number(settings: Settings) -> int:
    return logging.getLevelNamesMapping()[settings.log_level]
