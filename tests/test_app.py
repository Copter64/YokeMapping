from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from yokemapping import __version__, app
from yokemapping.config import Settings


def test_main_starts_and_shuts_down_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    assert app.main([]) == 0
    err = capsys.readouterr().err
    assert f"YokeMapping {__version__} starting" in err
    assert "shut down (exit code 0)" in err


def test_log_level_flag_overrides_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert app.main(["--log-level", "debug"]) == 0
    assert "Settings: Settings(" in capsys.readouterr().err


def test_config_error_exits_with_code_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert app.main(["--config", str(tmp_path / "missing.toml")]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_unhandled_error_is_logged_and_returns_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(settings: Settings) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(app, "run", boom)
    assert app.main([]) == 1
    err = capsys.readouterr().err
    assert "Unhandled error" in err
    assert "kaboom" in err
    assert "shut down (exit code 1)" in err


def test_keyboard_interrupt_shuts_down_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def interrupt(settings: Settings) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(app, "run", interrupt)
    assert app.main([]) == 0
    err = capsys.readouterr().err
    assert "Interrupted by user" in err
    assert "shut down (exit code 0)" in err


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        app.main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_python_dash_m_entry_point_runs() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("YOKEMAPPING_")}
    result = subprocess.run(
        [sys.executable, "-m", "yokemapping"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "starting" in result.stderr
    assert "shut down (exit code 0)" in result.stderr
