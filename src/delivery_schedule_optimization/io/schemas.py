"""Contracts of the three input files and of the output files."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import pandas as pd


class InputError(ValueError):
    """An input file cannot be used: missing column, unreadable value, empty file."""


class InputName(StrEnum):
    """The three input files of a run, always the same (no reference data is stored by the application)."""

    SCHEDULE = "released_schedule"
    DISTANCES = "distances"
    RATE_SHEET = "rate_sheet"


REQUIRED_COLUMNS: dict[InputName, list[str]] = {
    InputName.SCHEDULE: ["date", "supplier_id", "material", "plant", "unloading_point", "schedule_quantity", "weight", "volume"],
    InputName.DISTANCES: ["supplier_id", "distance"],
    InputName.RATE_SHEET: ["km_up_to"],
}
PDF_MAGIC = b"%PDF"


def file_suffix(content: bytes) -> str:
    """Extension under which an uploaded input is stored: .pdf for a PDF (rate sheet), .csv otherwise."""
    return ".pdf" if content.startswith(PDF_MAGIC) else ".csv"


@dataclass(frozen=True)
class RateSheet:
    """The rate sheet read from a CSV or a PDF.

    `table` is the price grid in the wide CSV format (`km_up_to`, then one column per weight bracket). A PDF also gives
    the freight margin, the diesel surcharge and the validity date of the grid; a CSV does not (None).
    """

    table: pd.DataFrame
    file_format: str
    margin: float | None = None
    diesel_surcharge: float | None = None
    valid_from: str | None = None


NUMERIC_COLUMNS: dict[InputName, list[str]] = {
    InputName.SCHEDULE: ["schedule_quantity", "weight", "volume"],
    InputName.DISTANCES: [],
    InputName.RATE_SHEET: [],
}


class OutputName(StrEnum):
    """Files produced by a run (CSV), in the format of the project brief."""

    CONSOLIDATED = "consolidated_dataset"
    ANOMALIES = "anomaly_register"
    PROFILES = "sortie1_profils"
    TRUCK_CONTENTS = "sortie2_contenu_camions"
    TRUCKS = "sortie2_camions"
    ALLOCATIONS = "sortie2_affectations"
    SAVING = "sortie3_saving"
    CURRENT_DELIVERIES = "situation_actuelle_livraisons"
    CHECKS = "controles"
