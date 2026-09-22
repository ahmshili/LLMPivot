"""Logging setup.

Every routing decision (candidate selected, failure type, cooldown,
retry) is logged by Router and CooldownManager at INFO/WARNING level via
the standard library logging module. This function just configures a
readable, consistent format for all of it.
"""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    root_logger = logging.getLogger("ai_gateway")
    root_logger.setLevel(level)

    if root_logger.handlers:
        # Avoid duplicate handlers if configure_logging is called twice
        # (e.g. once by tests, once by the app).
        return

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
