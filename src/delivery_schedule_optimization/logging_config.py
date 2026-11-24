"""Logging of the application: one format for the console, and one log file per run."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(processName)s %(name)s - %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger once (console)."""
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, force=True)


@contextmanager
def run_log_file(path: Path) -> Iterator[None]:
    """Copy every log record emitted during the block into `path` (the log of one run)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
        handler.close()
