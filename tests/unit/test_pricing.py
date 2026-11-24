"""Rate sheet reading and price of one truck (RG11)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from delivery_schedule_optimization.domain.config import PlannerConfig
from delivery_schedule_optimization.io.schemas import InputName
from delivery_schedule_optimization.pricing.tariff import Tariff, load_rate_grid, rate_grid


def test_rate_grid_bounds_are_sorted_and_the_last_bracket_is_open(input_paths: dict[InputName, Path]) -> None:
    grid = load_rate_grid(input_paths[InputName.RATE_SHEET])
    assert grid.index.is_monotonic_increasing and grid.columns.is_monotonic_increasing
    assert np.isinf(grid.columns[-1])


def test_rate_grid_requires_the_distance_column() -> None:
    with pytest.raises(ValueError, match="km_up_to"):
        rate_grid(pd.DataFrame({"km": ["50"], "1000": [1.0]}))


@pytest.mark.parametrize(
    ("weight_kg", "price"),
    [(1_000, 146.86), (1_001, 181.87), (4_000, 321.97), (24_500, 331.41)],  # bracket "up to", flat rate above 4 000 kg
)
def test_price_of_one_truck_at_121_km(input_paths: dict[InputName, Path], weight_kg: float, price: float) -> None:
    tariff = Tariff(load_rate_grid(input_paths[InputName.RATE_SHEET]), PlannerConfig())
    assert float(tariff(121, weight_kg)) == pytest.approx(price, abs=0.01)


def test_price_factor_is_margin_times_diesel() -> None:
    assert PlannerConfig().price_factor == pytest.approx((1 + 2.3923) * 1.1943)
