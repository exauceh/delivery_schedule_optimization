"""Price grid and price of one truck (RG11)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from delivery_schedule_optimization.io.readers import read_rate_sheet
from delivery_schedule_optimization.io.schemas import InputName
from delivery_schedule_optimization.pricing.tariff import Tariff, rate_grid


def test_rate_grid_bounds_are_sorted_and_the_last_bracket_is_open(input_paths: dict[InputName, Path]) -> None:
    grid = rate_grid(read_rate_sheet(input_paths[InputName.RATE_SHEET]).table)
    assert grid.index.is_monotonic_increasing and grid.columns.is_monotonic_increasing
    assert np.isinf(grid.columns[-1]) and np.isinf(grid.index[-1])


def test_rate_grid_requires_the_distance_column() -> None:
    with pytest.raises(ValueError, match="km_up_to"):
        rate_grid(pd.DataFrame({"km": ["50"], "1000": [1.0]}))


@pytest.mark.parametrize(
    ("weight_kg", "price"),
    [(1_000, 146.86), (1_001, 181.87), (4_000, 321.97), (24_500, 331.41)],  # bracket "up to", flat rate above 4 000 kg
)
def test_price_of_one_truck_at_121_km(input_paths: dict[InputName, Path], weight_kg: float, price: float) -> None:
    sheet = read_rate_sheet(input_paths[InputName.RATE_SHEET])
    tariff = Tariff(rate_grid(sheet.table), sheet.price_factor)
    assert float(tariff(121, weight_kg)) == pytest.approx(price, abs=0.01)


def test_price_factor_is_margin_times_diesel(input_paths: dict[InputName, Path]) -> None:
    assert read_rate_sheet(input_paths[InputName.RATE_SHEET]).price_factor == pytest.approx((1 + 2.3923) * 1.1943)
