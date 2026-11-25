"""Imputation of the missing values (Milestone 1, DQ04 and DQ07).

The prudent choice (H05, H06): a full truck and the longest distance. The alternative, chosen by the user: medians of the
known values (lines of the same supplier x plant couple for a load, planning suppliers for a distance).
"""

from __future__ import annotations

import logging

import pandas as pd

from ..domain.config import COUPLE, MissingDistance
from .quality import AnomalyRegister

logger = logging.getLogger(__name__)


def impute_full_truck(schedule: pd.DataFrame, column: str, capacity: float, register: AnomalyRegister) -> pd.DataFrame:
    """DQ04 (H05): a missing or non-positive weight or volume becomes the capacity of one truck.

    Adds `<column>_imputed` (True on the completed rows).
    """
    result = schedule.copy()
    needs = result[column].isna() | (result[column] <= 0)
    result.loc[needs, column] = capacity
    result[f"{column}_imputed"] = needs
    register.missing_loads[column] = {"full_truck": int(needs.sum())}
    register.add("DQ04", "schedule", "warning", int(needs.sum()), f"{column} imputed (full truck: {capacity:g})")
    return result


def impute_couple_median(schedule: pd.DataFrame, column: str, capacity: float, register: AnomalyRegister) -> pd.DataFrame:
    """DQ04, median variant: a missing or non-positive weight or volume becomes the median of the known lines of the same
    supplier x plant couple; a couple without any known line gets a full truck.

    Adds `<column>_imputed` (True on the completed rows).
    """
    result = schedule.copy()
    needs = result[column].isna() | (result[column] <= 0)
    known = result.loc[~needs]
    couple_line = known.groupby(COUPLE)[column].median()
    from_couple = pd.Series(pd.MultiIndex.from_frame(result[COUPLE]).map(couple_line.to_dict()), index=result.index, dtype=float)
    by_couple = needs & from_couple.notna()
    full_truck = needs & ~by_couple
    result.loc[needs, column] = from_couple.fillna(capacity).clip(upper=capacity)[needs]
    result[f"{column}_imputed"] = needs
    register.missing_loads[column] = {"couple": int(by_couple.sum()), "full_truck": int(full_truck.sum())}
    register.add("DQ04", "schedule", "warning", int(needs.sum()),
                 f"{column} imputed (couple median: {int(by_couple.sum())} rows, full truck: {int(full_truck.sum())})")
    return result


def supplier_distances(schedule: pd.DataFrame, distances: pd.DataFrame, register: AnomalyRegister,
                       method: MissingDistance = "max") -> pd.DataFrame:
    """DQ07: distance of every supplier of the planning; a missing one gets the longest (H06, `method="max"`) or the
    median (`method="median"`) distance of the planning suppliers that have one.

    Returns one row per supplier: `supplier_id`, `distance`, `distance_imputed`.
    """
    suppliers = pd.DataFrame({"supplier_id": sorted(schedule["supplier_id"].unique())})
    result = suppliers.merge(distances[["supplier_id", "distance"]], on="supplier_id", how="left")
    if result["distance"].isna().all():
        raise ValueError("distances: no supplier of the planning has a known distance")
    fallback = float(result["distance"].max() if method == "max" else result["distance"].median())
    result["distance_imputed"] = result["distance"].isna()
    result["distance"] = result["distance"].fillna(fallback)
    missing = result.loc[result["distance_imputed"], "supplier_id"].tolist()
    statistic = "maximum" if method == "max" else "median"
    register.missing_distances = missing
    register.distance_fallbacks = {"max": float(result["distance"].max()), "median": float(result.loc[~result["distance_imputed"], "distance"].median())}
    register.add("DQ07", "distances", "warning", len(missing),
                 f"{len(missing)} supplier(s) imputed with the {statistic} of the planning distances ({fallback} km)")
    logger.info("Fallback distance: %s km, suppliers: %s", fallback, missing)
    return result
