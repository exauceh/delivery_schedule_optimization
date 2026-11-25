"""Writing of the output tables (CSV, same format as the milestone 3 notebook), of the Excel report and of the zip archive."""

from __future__ import annotations

import logging
import zipfile
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

from .schemas import WORKBOOK_NAME, OutputName

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


# Sheets of the Excel report, in this order (sheet names are limited to 31 characters)
WORKBOOK_SHEETS: list[tuple[OutputName, str]] = [
    (OutputName.PROFILES, "Sortie1_Profils"),
    (OutputName.TRUCK_CONTENTS, "Sortie2_Contenu_camions"),
    (OutputName.TRUCKS, "Sortie2_Camions"),
    (OutputName.ALLOCATIONS, "Sortie2_Affectations"),
    (OutputName.SAVING, "Sortie3_Saving"),
    (OutputName.SMOOTHING_PLANTS, "Lissage_usines"),
    (OutputName.SMOOTHING_COUPLES, "Lissage_couples"),
    (OutputName.CURRENT_DELIVERIES, "Situation_actuelle"),
    (OutputName.ANOMALIES, "Anomalies"),
    (OutputName.CHECKS, "Controles"),
    (OutputName.OUTPUT_TESTS, "Tests_sorties"),
]


def write_workbook(tables: Mapping[OutputName, pd.DataFrame], summary: pd.DataFrame, directory: Path) -> Path:
    """Excel report of a run: a summary sheet, then one sheet per output table (those present in `tables`)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / WORKBOOK_NAME
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Synthese", index=False)
        for name, sheet in WORKBOOK_SHEETS:
            if name in tables:
                tables[name].to_excel(writer, sheet_name=sheet, index=False)
    logger.info("Excel report written to %s", path)
    return path


def zip_directory(directory: Path, archive: Path) -> Path:
    """Zip every file of `directory` (flat) into `archive`."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(directory.iterdir()):
            if path.is_file():
                zf.write(path, arcname=path.name)
    return archive
