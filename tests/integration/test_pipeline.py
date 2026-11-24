"""End-to-end run of the pipeline on the small planning: every rule holds and the outputs are consistent."""

from __future__ import annotations

import pandas as pd
import pytest

from delivery_schedule_optimization.domain.config import PlannerConfig
from delivery_schedule_optimization.io.readers import read_distances, read_rate_sheet, read_schedule
from delivery_schedule_optimization.io.schemas import InputName, OutputName
from delivery_schedule_optimization.pipeline import PipelineResult, RawInputs, RunConfig, run_pipeline


@pytest.fixture(scope="module")
def result(small_inputs: dict[InputName, bytes]) -> PipelineResult:
    inputs = RawInputs(
        schedule=read_schedule(small_inputs[InputName.SCHEDULE]),
        distances=read_distances(small_inputs[InputName.DISTANCES]),
        rate_sheet=read_rate_sheet(small_inputs[InputName.RATE_SHEET]),
    )
    return run_pipeline(inputs, RunConfig(planner=PlannerConfig(workers=1)))


def test_every_business_rule_is_respected(result: PipelineResult) -> None:
    checks = result.tables[OutputName.CHECKS]
    assert result.checks_passed, checks.loc[~checks["respecté"], "contrôle"].tolist()


def test_every_line_is_delivered_once(result: PipelineResult) -> None:
    allocations = result.tables[OutputName.ALLOCATIONS]
    assert allocations["line_id"].is_unique
    assert len(allocations) == len(result.tables[OutputName.CONSOLIDATED])


def test_saving_adds_up_and_is_not_negative(result: PipelineResult) -> None:
    saving = result.tables[OutputName.SAVING]
    assert saving["saving"].sum() == pytest.approx(result.kpis["saving"])
    assert result.kpis["cost_after"] <= result.kpis["cost_before"]


def test_one_profile_per_couple(result: PipelineResult) -> None:
    profiles = result.tables[OutputName.PROFILES]
    assert len(profiles) == result.kpis["couples"] == profiles[["supplier_id", "plant"]].drop_duplicates().shape[0]
    assert profiles["profile"].between(1, 5).all()


def test_timings_cover_every_stage(result: PipelineResult) -> None:
    assert set(result.timings) == {"preparation", "current_situation", "candidate_plans", "profiles_and_smoothing", "outputs", "checks"}


def test_same_inputs_give_the_same_results(small_inputs: dict[InputName, bytes], result: PipelineResult) -> None:
    """Reproducibility: same input and parameters, same outputs."""
    again = run_pipeline(RawInputs(read_schedule(small_inputs[InputName.SCHEDULE]), read_distances(small_inputs[InputName.DISTANCES]),
                                   read_rate_sheet(small_inputs[InputName.RATE_SHEET])), RunConfig(planner=PlannerConfig(workers=1)))
    for name in (OutputName.PROFILES, OutputName.TRUCKS, OutputName.ALLOCATIONS):
        pd.testing.assert_frame_equal(again.tables[name], result.tables[name])
