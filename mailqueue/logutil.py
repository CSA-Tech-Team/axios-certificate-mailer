"""Timestamped logging to a rotating file and the console. Never log credentials."""
from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime

from .config import Config

LOGGER_NAME = "mailq"


class _IsoFormatter(logging.Formatter):
    def __init__(self, fmt, tz):
        super().__init__(fmt)
        self._tz = tz

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, tz=self._tz).isoformat(timespec="seconds")


def setup_logging(cfg: Config, console: bool = True) -> logging.Logger:
    log = logging.getLogger(LOGGER_NAME)
    log.setLevel(logging.INFO)
    log.propagate = False
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    fmt = _IsoFormatter("%(asctime)s %(levelname)s %(message)s", cfg.tz())
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        cfg.log_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        log.addHandler(ch)
    return log
