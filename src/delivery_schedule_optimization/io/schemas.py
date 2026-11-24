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
    """Extension under which an uploaded input is stored: .pdf for the rate sheet, .csv for the two others."""
    return ".pdf" if content.startswith(PDF_MAGIC) else ".csv"


@dataclass(frozen=True)
class RateSheet:
    """The carrier's rate sheet, read from its PDF: price grid and terms (RG11).

    `table` is the price grid in a wide format: `km_up_to` (distance bracket label, "30" ... ">650"), then one column per
    weight bracket ("50" ... ">4000") with the freight cost of one shipment. The price of a truck is that cost
    x (1 + margin) x diesel surcharge.
    """

    table: pd.DataFrame
    margin: float  # freight value margin: 2.3923 for 239.23 %
    diesel_surcharge: float
    valid_from: str | None = None

    @property
    def price_factor(self) -> float:
        return (1 + self.margin) * self.diesel_surcharge


NUMERIC_COLUMNS: dict[InputName, list[str]] = {
    InputName.SCHEDULE: ["schedule_quantity", "weight", "volume"],
    InputName.DISTANCES: [],
    InputName.RATE_SHEET: [],
}


WORKBOOK_NAME = "resultats_consolidation.xlsx"  # Excel report of a run, next to the CSV tables


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
    CURRENT_TRUCKS = "situation_actuelle_camions"
    SMOOTHING_PLANTS = "lissage_usines"
    SMOOTHING_COUPLES = "lissage_couples"
    CHECKS = "controles"
    OUTPUT_TESTS = "tests_sorties"
