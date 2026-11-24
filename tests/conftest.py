"""Shared fixtures: the real input files and a small planning cut from them (fast end-to-end runs)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from delivery_schedule_optimization.io.schemas import InputName

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


@pytest.fixture(scope="session")
def input_paths() -> dict[InputName, Path]:
    """The three real input files of the project: November 2026 planning, distances and the carrier's rate sheet (PDF)."""
    return {InputName.SCHEDULE: DOCS / "released_schedule.csv", InputName.DISTANCES: DOCS / "distances.csv",
            InputName.RATE_SHEET: ROOT / "courses" / "rate_sheet.pdf"}


@pytest.fixture(scope="session")
def small_inputs(input_paths: dict[InputName, Path]) -> dict[InputName, bytes]:
    """A small planning with the edge cases of the real one, as uploaded bytes.

    Suppliers: SUP_00085 (needs every working day), SUP_00130 (no known distance, DQ07) and the first supplier with a
    missing weight (DQ04). Their quantity-0 rows (DQ01) and the cumulative 01/01/2027 rows (DQ03) are kept.
    """
    schedule = pd.read_csv(input_paths[InputName.SCHEDULE], encoding="utf-8-sig")
    weight = pd.to_numeric(schedule["weight"], errors="coerce")
    missing_weight = schedule.loc[(weight.isna() | (weight <= 0)) & (schedule["schedule_quantity"] > 0), "supplier_id"]
    suppliers = sorted({"SUP_00085", "SUP_00130", str(missing_weight.iloc[0])})
    small = schedule[schedule["supplier_id"].isin(suppliers)]
    return {
        InputName.SCHEDULE: small.to_csv(index=False).encode("utf-8"),
        InputName.DISTANCES: input_paths[InputName.DISTANCES].read_bytes(),
        InputName.RATE_SHEET: input_paths[InputName.RATE_SHEET].read_bytes(),
    }
