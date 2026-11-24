"""Contracts of the three input files and of the output files."""

from __future__ import annotations

from enum import StrEnum


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
