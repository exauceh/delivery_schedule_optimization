"""Contracts of the input files and of the run parameters."""

from __future__ import annotations

import pytest

from delivery_schedule_optimization.domain.config import PlannerConfig
from delivery_schedule_optimization.domain.parameters import ParameterError, dump_parameters, parse_parameters
from delivery_schedule_optimization.io.readers import read_distances, read_rate_sheet, read_schedule
from delivery_schedule_optimization.io.schemas import InputError


def test_schedule_with_a_missing_column_is_rejected() -> None:
    with pytest.raises(InputError, match="weight"):
        read_schedule(b"date,supplier_id,material,plant,unloading_point,schedule_quantity,volume\n2026-11-09,A,M,P,U,1,1\n")


def test_non_numeric_weight_is_rejected() -> None:
    content = b"date,supplier_id,material,plant,unloading_point,schedule_quantity,weight,volume\n2026-11-09,A,M,P,U,1,heavy,1\n"
    with pytest.raises(InputError, match="non-numeric"):
        read_schedule(content)


def test_empty_weight_is_kept_for_the_imputation() -> None:
    content = b"date,supplier_id,material,plant,unloading_point,schedule_quantity,weight,volume\n2026-11-09,A,M,P,U,1,,1\n"
    assert read_schedule(content)["weight"].isna().all()


def test_distances_and_rate_sheet_need_their_key_column() -> None:
    with pytest.raises(InputError):
        read_distances(b"supplier,km\nA,10\n")
    with pytest.raises(InputError):
        read_rate_sheet(b"km,1000\n50,10\n")


def test_overrides_keep_the_other_defaults() -> None:
    planner, preparation = parse_parameters({"planner": {"smoothing_budget": 0.005, "allowed_frequencies": [1, 2, 3]}})
    assert planner.smoothing_budget == 0.005 and planner.allowed_frequencies == (1, 2, 3)
    assert planner.truck_kg == PlannerConfig().truck_kg and preparation.tail_date is None


@pytest.mark.parametrize("parameters", [{"planner": {"unknown": 1}}, {"other": {}}, {"planner": {"max_advance_days": "seven"}}])
def test_invalid_parameters_are_rejected(parameters: dict[str, object]) -> None:
    with pytest.raises(ParameterError):
        parse_parameters(parameters)  # type: ignore[arg-type]


def test_dumped_parameters_can_be_parsed_back() -> None:
    planner, preparation = parse_parameters({"planner": {"objective": "trucks"}})
    assert parse_parameters(dump_parameters(planner, preparation)) == (planner, preparation)
