from __future__ import annotations

import logging
from pathlib import Path

from yokemapping.logging_setup import LOG_FILE_NAME, configure_logging


def test_file_logging_writes_to_log_dir(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs" / "nested"
    configure_logging(logging.INFO, log_dir)
    logging.getLogger("yokemapping.test").info("hello file")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "hello file" in (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")


def test_reconfiguring_does_not_duplicate_handlers(tmp_path: Path) -> None:
    configure_logging(logging.INFO, tmp_path)
    configure_logging(logging.DEBUG, tmp_path)
    root = logging.getLogger()
    assert len(root.handlers) == 2
    assert root.level == logging.DEBUG


def test_console_only_when_no_log_dir() -> None:
    configure_logging(logging.WARNING)
    assert len(logging.getLogger().handlers) == 1
