from __future__ import annotations

from pathlib import Path

import pytest

from yokemapping.config import ConfigError, Settings, load_settings


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_when_no_sources(tmp_path: Path) -> None:
    assert load_settings(environ={}) == Settings()


def test_toml_overrides_defaults(tmp_path: Path) -> None:
    cfg = _write(tmp_path / "cfg.toml", 'log_level = "DEBUG"\nsimconnect_port = 501\n')
    settings = load_settings(cfg, environ={})
    assert settings.log_level == "DEBUG"
    assert settings.simconnect_port == 501
    assert settings.simconnect_host == "localhost"


def test_implicit_config_file_in_cwd_is_used(tmp_path: Path) -> None:
    _write(tmp_path / "yokemapping.toml", "steamvr_camera_index = 2\n")
    assert load_settings(environ={}).steamvr_camera_index == 2


def test_precedence_toml_then_dotenv_then_environ(tmp_path: Path) -> None:
    cfg = _write(tmp_path / "cfg.toml", 'log_level = "DEBUG"\nsimconnect_port = 501\n')
    _write(
        tmp_path / ".env",
        "YOKEMAPPING_SIMCONNECT_PORT=502\nYOKEMAPPING_SIMCONNECT_HOST=dotenv-host\n",
    )
    settings = load_settings(cfg, environ={"YOKEMAPPING_SIMCONNECT_HOST": "env-host"})
    assert settings.log_level == "DEBUG"  # only set in TOML
    assert settings.simconnect_port == 502  # .env beats TOML
    assert settings.simconnect_host == "env-host"  # real environment beats .env


def test_unprefixed_env_vars_are_ignored() -> None:
    assert load_settings(environ={"LOG_LEVEL": "DEBUG"}).log_level == "INFO"


def test_dotenv_can_be_disabled(tmp_path: Path) -> None:
    _write(tmp_path / ".env", "YOKEMAPPING_LOG_LEVEL=DEBUG\n")
    assert load_settings(environ={}, dotenv_path=None).log_level == "INFO"


def test_env_values_are_coerced() -> None:
    settings = load_settings(
        environ={
            "YOKEMAPPING_LOG_LEVEL": "warning",
            "YOKEMAPPING_LOG_DIR": "logs",
            "YOKEMAPPING_SIMCONNECT_PORT": "600",
            "YOKEMAPPING_STEAMVR_CAMERA_INDEX": "1",
            "YOKEMAPPING_CALIBRATION_PROFILE_DIR": "profiles",
        }
    )
    assert settings == Settings(
        log_level="WARNING",
        log_dir=Path("logs"),
        simconnect_port=600,
        steamvr_camera_index=1,
        calibration_profile_dir=Path("profiles"),
    )


def test_empty_log_dir_means_console_only() -> None:
    assert load_settings(environ={"YOKEMAPPING_LOG_DIR": ""}).log_dir is None


def test_missing_explicit_config_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "nope.toml", environ={})


def test_unknown_toml_key_is_an_error(tmp_path: Path) -> None:
    cfg = _write(tmp_path / "cfg.toml", "simconect_port = 500\n")
    with pytest.raises(ConfigError, match="simconect_port"):
        load_settings(cfg, environ={})


def test_malformed_toml_is_an_error(tmp_path: Path) -> None:
    cfg = _write(tmp_path / "cfg.toml", "log_level = \n")
    with pytest.raises(ConfigError, match="could not parse"):
        load_settings(cfg, environ={})


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"YOKEMAPPING_SIMCONNECT_PORT": "abc"}, "simconnect_port"),
        ({"YOKEMAPPING_SIMCONNECT_PORT": "0"}, "1-65535"),
        ({"YOKEMAPPING_SIMCONNECT_PORT": "70000"}, "1-65535"),
        ({"YOKEMAPPING_STEAMVR_CAMERA_INDEX": "-1"}, ">= 0"),
        ({"YOKEMAPPING_LOG_LEVEL": "LOUD"}, "log_level"),
    ],
)
def test_invalid_env_values(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_settings(environ=env)


@pytest.mark.parametrize(
    "toml_text",
    [
        "simconnect_port = true\n",
        'simconnect_port = "x"\n',
        "simconnect_host = 5\n",
        "calibration_profile_dir = 5\n",
    ],
)
def test_wrong_toml_types(tmp_path: Path, toml_text: str) -> None:
    cfg = _write(tmp_path / "cfg.toml", toml_text)
    with pytest.raises(ConfigError, match="invalid value"):
        load_settings(cfg, environ={})


def test_example_config_file_is_valid() -> None:
    example = Path(__file__).resolve().parents[1] / "yokemapping.example.toml"
    assert load_settings(example, environ={}) == Settings()
