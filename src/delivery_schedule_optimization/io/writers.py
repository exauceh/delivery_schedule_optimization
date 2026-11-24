"""Writing of the output tables (CSV, same format as the milestone 3 notebook) and of the zip archive of a run."""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from .schemas import OutputName

logger = logging.getLogger(__name__)


def write_tables(tables: Mapping[OutputName, pd.DataFrame], directory: Path) -> dict[OutputName, Path]:
    """Write every table as `<name>.csv` (dates as YYYY-MM-DD) and return the paths."""
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[OutputName, Path] = {}
    for name, frame in tables.items():
        path = directory / f"{name}.csv"
        frame.to_csv(path, index=False, date_format="%Y-%m-%d")
        paths[name] = path
    logger.info("%d tables written to %s", len(paths), directory)
    return paths


def zip_directory(directory: Path, archive: Path) -> Path:
    """Zip every file of `directory` (flat) into `archive`."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(directory.iterdir()):
            if path.is_file():
                zf.write(path, arcname=path.name)
    return archive
