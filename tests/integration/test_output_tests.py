"""Milestone 4, suites A2 to A5 on the outputs of a run: every test passes, and every test detects a broken output.

A test that always answers True proves nothing: each test is also run on a copy of the output broken on purpose.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import pytest

from delivery_schedule_optimization.domain.calendar import public_holidays
from delivery_schedule_optimization.domain.config import PlannerConfig
from delivery_schedule_optimization.io.readers import read_rate_sheet
from delivery_schedule_optimization.io.schemas import InputName, OutputName
from delivery_schedule_optimization.pipeline import PipelineResult
from delivery_schedule_optimization.pricing.tariff import Tariff, rate_grid
from delivery_schedule_optimization.validation import output_tests as ot

CONFIG = PlannerConfig(workers=1)


@pytest.fixture(scope="module")
def result(small_result: PipelineResult) -> PipelineResult:
    return small_result


@pytest.fixture(scope="module")
def tariff(small_inputs: dict[InputName, bytes]) -> Tariff:
    sheet = read_rate_sheet(small_inputs[InputName.RATE_SHEET])
    return Tariff(rate_grid(sheet.table), sheet.price_factor)


def test_every_output_test_passes_on_a_run(result: PipelineResult) -> None:
    tests = result.tables[OutputName.OUTPUT_TESTS]
    assert len(tests) == 24 and set(tests.columns) == set(ot.RESULT_COLUMNS)
    failed = tests.loc[~tests["respecté"], ["fonction", "détail"]]
    assert failed.empty, failed.to_dict("records")
    assert result.checks_passed


def test_a_crashing_test_is_a_failed_test() -> None:
    def test_crash(_: object) -> ot.TestOutcome:
        """Exemple : un test qui plante"""
        raise KeyError("column")

    row = ot.run_test("exemple", test_crash, None)
    assert row["respecté"] is False and row["test"] == "Exemple : un test qui plante" and "KeyError" in str(row["détail"])


def _broken(frame: pd.DataFrame, change: Callable[[pd.DataFrame], None]) -> pd.DataFrame:
    copy = frame.copy()
    change(copy)
    return copy


def test_each_constraint_test_detects_a_broken_output(result: PipelineResult, tariff: Tariff) -> None:
    alloc = result.tables[OutputName.ALLOCATIONS]
    trucks = result.tables[OutputName.TRUCKS]
    contents = result.tables[OutputName.TRUCK_CONTENTS]
    first = alloc.index[0]
    other_couple_truck = alloc.loc[alloc["supplier_id"] != alloc.loc[first, "supplier_id"], "truck_id"].iloc[0]
    biggest = alloc.groupby("truck_id").size().idxmax()

    def early(f: pd.DataFrame) -> None:
        f.loc[first, "delivery_date"] = f.loc[first, "need_date"] - pd.Timedelta(days=CONFIG.max_advance_days + 1)

    def late(f: pd.DataFrame) -> None:
        f.loc[first, "delivery_date"] = f.loc[first, "need_date"] + pd.Timedelta(days=1)

    def all_late(f: pd.DataFrame) -> None:  # every delivery of one plant x material one day late: a stock-out
        material = (f["plant"] == f.loc[first, "plant"]) & (f["material"] == f.loc[first, "material"])
        f.loc[material, "delivery_date"] = f.loc[material, "need_date"] + pd.Timedelta(days=1)

    detected = {
        "RG01": not ot.test_rg01_one_couple_per_truck(_broken(alloc, lambda f: f.__setitem__("truck_id", f["truck_id"].where(
            f.index != first, other_couple_truck))))[0],
        "RG02": not ot.test_rg02_never_late(_broken(alloc, late))[0],
        "RG03": not ot.test_rg03_max_advance(_broken(alloc, early), CONFIG.max_advance_days)[0],
        "RG03 colonne": not ot.test_rg03_advance_column(_broken(alloc, early))[0],
        "RG04 poids": not ot.test_rg04_weight(_broken(alloc, lambda f: f.__setitem__("weight", f["weight"].where(
            f["truck_id"] != biggest, CONFIG.truck_kg))), CONFIG.truck_kg)[0],
        "RG04 volume": not ot.test_rg04_volume(_broken(alloc, lambda f: f.__setitem__("volume", f["volume"].where(
            f["truck_id"] != biggest, CONFIG.truck_m3))), CONFIG.truck_m3)[0],
        "RG08 doublon": not ot.test_each_line_delivered_once(pd.concat([alloc, alloc.head(1)]))[0],
        "RG08 oubli": not ot.test_no_line_forgotten(alloc.iloc[1:], alloc)[0],
        "RG11 coût": not ot.test_rg11_truck_cost(_broken(trucks, lambda f: f.__setitem__("cost", f["cost"] * 1.01)), tariff)[0],
        "RG11 totaux": not ot.test_rg11_truck_totals(_broken(trucks, lambda f: f.__setitem__("weight", f["weight"] + 1)), alloc)[0],
        "contenu": not ot.test_contents_match_allocations(_broken(contents, lambda f: f.__setitem__("quantity", f["quantity"] + 1)),
                                                          alloc)[0],
        "rupture": not ot.test_no_stock_out(_broken(alloc, all_late))[0],
        "rythme hebdomadaire": not ot.test_weekly_patterns_add_up(
            _broken(result.tables[OutputName.PROFILES], lambda f: f.__setitem__("weekly_pattern", f["weekly_pattern"].astype(str) + " 1")),
            result.tables[OutputName.SAVING])[0],
    }
    assert all(detected.values()), [name for name, ok in detected.items() if not ok]


def test_profile_tests_detect_a_delivery_off_the_profile(result: PipelineResult) -> None:
    alloc = result.tables[OutputName.ALLOCATIONS]
    profiles = result.tables[OutputName.PROFILES]
    holidays = public_holidays(alloc.rename(columns={"need_date": "date"}), CONFIG)
    one_day = profiles.loc[profiles["profile"].eq(1), ["supplier_id", "plant"]].iloc[0]  # a couple delivered one day a week
    first = alloc.index[(alloc["supplier_id"] == one_day["supplier_id"]) & (alloc["plant"] == one_day["plant"])][0]
    day = alloc.loc[first, "profile_day"]
    other_day = day + pd.Timedelta(days=1 if day.weekday() < 4 else -1)  # another weekday, outside its profile
    shifted = alloc.copy()
    shifted.loc[first, ["delivery_date", "profile_day"]] = other_day
    assert not ot.test_same_days_profile(shifted, profiles)[0]
    no_profile_day = alloc.copy()
    no_profile_day.loc[first, "profile_day"] = pd.NaT
    assert not ot.test_every_delivery_on_a_profile_day(no_profile_day, holidays)[0]
