"""Shared fixtures: the real input files and a small planning cut from them (fast end-to-end runs)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from delivery_schedule_optimization.domain.calendar import public_holidays
from delivery_schedule_optimization.domain.config import PlannerConfig, PreparationConfig
from delivery_schedule_optimization.io.readers import read_distances, read_rate_sheet, read_schedule
from delivery_schedule_optimization.io.schemas import InputName
from delivery_schedule_optimization.pipeline import PipelineResult, RawInputs, RunConfig, run_pipeline
from delivery_schedule_optimization.preparation.lines import prepare_lines
from delivery_schedule_optimization.preparation.pipeline import prepare
from delivery_schedule_optimization.pricing.tariff import Tariff, rate_grid
from delivery_schedule_optimization.validation.engine_cases import EngineInputs

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


@pytest.fixture(scope="session")
def engine_inputs(input_paths: dict[InputName, Path]) -> EngineInputs:
    config = PlannerConfig()
    prepared = prepare(read_schedule(input_paths[InputName.SCHEDULE]), read_distances(input_paths[InputName.DISTANCES]), config,
                       PreparationConfig())
    lines = prepare_lines(prepared.consolidated, config)
    sheet = read_rate_sheet(input_paths[InputName.RATE_SHEET])
    return EngineInputs(lines, public_holidays(lines, config), Tariff(rate_grid(sheet.table), sheet.price_factor), config)


@pytest.fixture(scope="session")
def small_result(small_inputs: dict[InputName, bytes]) -> PipelineResult:
    """One run of the pipeline on the small planning, in this process (shared by the integration tests)."""
    inputs = RawInputs(read_schedule(small_inputs[InputName.SCHEDULE]), read_distances(small_inputs[InputName.DISTANCES]),
                       read_rate_sheet(small_inputs[InputName.RATE_SHEET]))
    return run_pipeline(inputs, RunConfig(planner=PlannerConfig(workers=1)))
