from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
LOG_FILE_NAME = "yokemapping.log"


def configure_logging(level: int, log_dir: Path | None = None) -> None:
    """Configure root logging to stderr and, optionally, a rotating file in ``log_dir``.

    Safe to call more than once; previously installed handlers are replaced.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_dir / LOG_FILE_NAME, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
            )
        )
    logging.basicConfig(level=level, format=LOG_FORMAT, handlers=handlers, force=True)
