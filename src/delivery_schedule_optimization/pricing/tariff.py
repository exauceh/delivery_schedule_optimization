"""Rate sheet and price of one truck (RG11)."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pandas as pd


def _bound(text: object) -> float:
    """Upper bound of a bracket label: "> 4000" is open (inf), any other label is a number."""
    return np.inf if str(text).strip().startswith(">") else float(str(text))


def rate_grid(raw: pd.DataFrame) -> pd.DataFrame:
    """Wide rate sheet (one row per distance bracket `km_up_to`, one column per weight bracket) -> numeric grid.

    The grid is indexed by the km upper bound and its columns are the kg upper bounds (inf = open bracket).
    """
    if "km_up_to" not in raw.columns:
        raise ValueError("rate sheet misses the column 'km_up_to'")
    grid = raw.astype({"km_up_to": str}).set_index("km_up_to").astype(float)
    grid.index = pd.Index([_bound(k) for k in grid.index])
    grid.columns = pd.Index([_bound(k) for k in grid.columns])
    return grid.sort_index().sort_index(axis=1)


class Tariff:
    """Price of ONE truck: grid(distance bracket, truck weight bracket) x price factor (RG11).

    `price_factor` = (1 + margin) x diesel surcharge, both read from the rate sheet (`RateSheet.price_factor`).
    """

    def __init__(self, grid: pd.DataFrame, price_factor: float) -> None:
        self.km: npt.NDArray[np.float64] = grid.index.to_numpy(dtype=float)
        self.kg: npt.NDArray[np.float64] = grid.columns.to_numpy(dtype=float)
        self.prices: npt.NDArray[np.float64] = grid.to_numpy(dtype=float) * price_factor

    def __call__(self, distance_km: npt.ArrayLike, weight_kg: npt.ArrayLike) -> npt.NDArray[np.float64]:
        row = np.searchsorted(self.km, np.asarray(distance_km, dtype=float), side="left")
        col = np.searchsorted(self.kg, np.asarray(weight_kg, dtype=float), side="left")
        prices: npt.NDArray[np.float64] = self.prices[row, col]
        return prices
