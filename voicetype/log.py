"""Logging setup: stderr (picked up by journald under systemd) + rotating file."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

LOGGER_NAME = "voicetype"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5


def default_log_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "voicetype"


def setup_logging(level: str = "INFO", log_file: Path | None | bool = None) -> logging.Logger:
    """Configure the ``voicetype`` logger. ``log_file=None`` uses the default path,
    ``False`` disables file logging. Idempotent."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(fmt)
    logger.addHandler(stderr)

    if log_file is not False:
        path = Path(log_file) if log_file else default_log_dir() / "voicetype.log"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
            )
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except OSError as e:  # read-only home etc. — never fatal
            logger.warning("file logging disabled: %s", e)
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
