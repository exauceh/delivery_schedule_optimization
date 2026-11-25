"""Imputation of the missing values (Milestone 1, DQ04 and DQ07).

The prudent choice (H05, H06): a full truck and the longest distance. The alternative, chosen by the user: medians of the
known values (per piece of the same material for a load, of the planning suppliers for a distance).
"""

from __future__ import annotations

import logging

import pandas as pd

from ..domain.config import MissingDistance
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


def impute_material_median(schedule: pd.DataFrame, column: str, capacity: float, register: AnomalyRegister) -> pd.DataFrame:
    """DQ04, median variant: a missing or non-positive weight or volume becomes quantity x the median per piece of the
    same material on its known lines; a material without any known line gets the median line of its supplier, and a
    supplier without any, a full truck. Never more than one truck.

    Adds `<column>_imputed` (True on the completed rows).
    """
    result = schedule.copy()
    needs = result[column].isna() | (result[column] <= 0)
    known = result.loc[~needs]
    per_piece = (known[column] / known["schedule_quantity"]).groupby(known["material"]).median()
    supplier_line = known.groupby("supplier_id")[column].median()
    from_material = result["material"].map(per_piece) * result["schedule_quantity"]
    from_supplier = result["supplier_id"].map(supplier_line)
    value = from_material.fillna(from_supplier).fillna(capacity).clip(upper=capacity)
    by_material = needs & from_material.notna()
    by_supplier = needs & from_material.isna() & from_supplier.notna()
    full_truck = needs & ~by_material & ~by_supplier
    result.loc[needs, column] = value[needs]
    result[f"{column}_imputed"] = needs
    register.missing_loads[column] = {"material": int(by_material.sum()), "supplier": int(by_supplier.sum()), "full_truck": int(full_truck.sum())}
    register.add("DQ04", "schedule", "warning", int(needs.sum()),
                 f"{column} imputed (material median: {int(by_material.sum())} rows, supplier median: {int(by_supplier.sum())}, "
                 f"full truck: {int(full_truck.sum())})")
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
