"""Preparation of the data (Milestone 1 cleaning): rules DQ01 to DQ07 and the consolidated dataset."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from delivery_schedule_optimization.domain.config import PlannerConfig, PreparationConfig
from delivery_schedule_optimization.io.readers import read_distances, read_schedule
from delivery_schedule_optimization.io.schemas import InputName
from delivery_schedule_optimization.preparation.cleaning import tail_date
from delivery_schedule_optimization.preparation.imputation import impute_full_truck, impute_material_median, supplier_distances
from delivery_schedule_optimization.preparation.pipeline import prepare
from delivery_schedule_optimization.preparation.quality import AnomalyRegister

ROOT = Path(__file__).resolve().parents[2]


def _schedule(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.to_datetime(dates)})


def test_tail_date_is_detected_after_a_long_gap() -> None:
    schedule = _schedule(["2026-12-17", "2026-12-18", "2027-01-01"])
    assert tail_date(schedule, PreparationConfig()) == pd.Timestamp("2027-01-01")


def test_no_tail_date_when_the_dates_are_contiguous() -> None:
    assert tail_date(_schedule(["2026-12-17", "2026-12-18", "2026-12-21"]), PreparationConfig()) is None


def test_tail_date_can_be_forced_or_disabled() -> None:
    schedule = _schedule(["2026-12-17", "2027-01-01"])
    assert tail_date(schedule, PreparationConfig(tail_date="2026-12-17")) == pd.Timestamp("2026-12-17")
    assert tail_date(schedule, PreparationConfig(tail_date="")) is None


def test_missing_or_zero_weight_becomes_a_full_truck() -> None:
    schedule = pd.DataFrame({"weight": [np.nan, 0.0, 120.0]})
    result = impute_full_truck(schedule, "weight", 24_500.0, AnomalyRegister())
    assert result["weight"].tolist() == [24_500.0, 24_500.0, 120.0]
    assert result["weight_imputed"].tolist() == [True, True, False]


def test_missing_distance_gets_the_longest_planning_distance() -> None:
    schedule = pd.DataFrame({"supplier_id": ["A", "B", "C"]})
    distances = pd.DataFrame({"supplier_id": ["A", "B", "Z"], "distance": [30.0, 80.0, 500.0]})  # Z is not in the planning
    result = supplier_distances(schedule, distances, AnomalyRegister()).set_index("supplier_id")
    assert result.loc["C", "distance"] == 80.0 and bool(result.loc["C", "distance_imputed"])


def test_missing_load_from_the_median_of_the_material() -> None:
    """Material median, else the median line of the supplier, else a full truck; never more than one truck."""
    schedule = pd.DataFrame({
        "supplier_id": ["S", "S", "S", "S", "S", "T", "S"],
        "material": ["A", "A", "A", "B", "C", "D", "A"],
        "schedule_quantity": [10.0, 30.0, 5.0, 4.0, 1.0, 2.0, 100_000.0],
        "weight": [20.0, 30.0, np.nan, np.nan, 100.0, 0.0, np.nan],  # A: 2 and 1 kg per piece, median 1.5
    })
    register = AnomalyRegister()
    result = impute_material_median(schedule, "weight", 24_500.0, register)
    assert result["weight"].tolist() == [20.0, 30.0, 7.5, 30.0, 100.0, 24_500.0, 24_500.0]  # B: median line of S (20, 30, 100)
    assert result["weight_imputed"].tolist() == [False, False, True, True, False, True, True]
    assert register.missing_loads["weight"] == {"material": 2, "supplier": 1, "full_truck": 1}


def test_missing_distance_can_get_the_median_distance() -> None:
    schedule = pd.DataFrame({"supplier_id": ["A", "B", "C", "D"]})
    distances = pd.DataFrame({"supplier_id": ["A", "B", "C"], "distance": [30.0, 80.0, 40.0]})
    register = AnomalyRegister()
    result = supplier_distances(schedule, distances, register, method="median").set_index("supplier_id")
    assert result.loc["D", "distance"] == 40.0 and bool(result.loc["D", "distance_imputed"])
    assert register.missing_distances == ["D"] and register.distance_fallbacks == {"max": 80.0, "median": 40.0}


def test_line_larger_than_a_truck_is_dropped() -> None:
    raw = pd.DataFrame({"date": pd.to_datetime(["2026-11-09", "2026-11-09"]), "supplier_id": ["A", "A"], "material": ["M1", "M2"],
                        "plant": ["P", "P"], "unloading_point": ["U", "U"], "schedule_quantity": [1.0, 1.0],
                        "weight": [30_000.0, 100.0], "volume": [1.0, 1.0]})
    prepared = prepare(raw, pd.DataFrame({"supplier_id": ["A"], "distance": [50.0]}), PlannerConfig(), PreparationConfig())
    assert prepared.consolidated["material"].tolist() == ["M2"]


def test_preparation_reproduces_the_milestone_1_dataset(input_paths: dict[InputName, Path]) -> None:
    """Same steps and order as the Milestone 1 notebook: the consolidated dataset is identical, byte for byte."""
    prepared = prepare(read_schedule(input_paths[InputName.SCHEDULE]), read_distances(input_paths[InputName.DISTANCES]), PlannerConfig(),
                       PreparationConfig())
    expected = (ROOT / "data" / "consolidated_dataset.csv").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert prepared.consolidated.to_csv(index=False).replace("\r\n", "\n") == expected
    register = prepared.anomalies.set_index(["rule", "action"])["rows"]
    assert register.xs("DQ03", level="rule").iloc[0] == 379  # cumulative needs of 01/01/2027
    assert register.xs("DQ07", level="rule").iloc[0] == 1  # SUP_00130 has no distance


def test_duplicated_need_is_rejected() -> None:
    row = {"date": pd.Timestamp("2026-11-09"), "supplier_id": "A", "material": "M", "plant": "P", "unloading_point": "U",
           "schedule_quantity": 1.0, "weight": 1.0, "volume": 1.0}
    with pytest.raises(ValueError, match="same need key"):
        prepare(pd.DataFrame([row, row]), pd.DataFrame({"supplier_id": ["A"], "distance": [10.0]}), PlannerConfig(), PreparationConfig())
