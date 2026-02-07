"""Centralized logging configuration."""

from __future__ import annotations

import logging
import sys


def setup_logging(verbosity: int = 0) -> None:
    """Configure logging for the application.

    Args:
        verbosity: 0 = WARNING, 1 (-v) = INFO, 2+ (-vv) = DEBUG.
    """
    if verbosity >= 2:
        level = logging.DEBUG
    elif verbosity >= 1:
        level = logging.INFO
    else:
        level = logging.WARNING

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger("atomcam_meteor")
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
