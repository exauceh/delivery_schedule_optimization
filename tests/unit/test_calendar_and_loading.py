"""Calendar of the plan (RG10) and loading of the trucks (RG04)."""

from __future__ import annotations

from datetime import date

import numpy as np

from delivery_schedule_optimization.domain.calendar import first_working_day, is_working_day, monday
from delivery_schedule_optimization.domain.config import PlannerConfig
from delivery_schedule_optimization.engine.loading import pack

CHRISTMAS = {date(2026, 12, 25), date(2026, 12, 26)}


def test_weekends_and_holidays_are_not_working_days() -> None:
    assert is_working_day(date(2026, 11, 9), CHRISTMAS)
    assert not is_working_day(date(2026, 11, 14), CHRISTMAS)  # Saturday
    assert not is_working_day(date(2026, 12, 25), CHRISTMAS)


def test_first_working_day_skips_holidays_and_weekends() -> None:
    assert first_working_day(date(2026, 12, 25), CHRISTMAS) == date(2026, 12, 28)


def test_monday_of_the_week() -> None:
    assert monday(date(2026, 11, 13)) == date(2026, 11, 9)


def test_pack_keeps_lines_whole_and_respects_both_capacities() -> None:
    config = PlannerConfig()
    weight = np.array([20_000.0, 10_000.0, 5_000.0, 4_000.0, 300.0])
    volume = np.array([10.0, 60.0, 50.0, 5.0, 1.0])
    trucks = pack(weight, volume, config)
    assert sorted(i for t in trucks for i in t) == list(range(len(weight)))  # every line once
    for t in trucks:
        assert weight[t].sum() <= config.truck_kg and volume[t].sum() <= config.truck_m3
    assert len(trucks) == 2  # minimum: ceil(39 300 / 24 500) = 2


def test_pack_one_full_truck_line_takes_a_truck_alone() -> None:
    config = PlannerConfig()
    trucks = pack(np.array([24_500.0, 100.0]), np.array([1.0, 1.0]), config)
    assert len(trucks) == 2
