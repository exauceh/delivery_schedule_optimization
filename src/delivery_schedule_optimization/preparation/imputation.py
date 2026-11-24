"""Imputation of the missing values (Milestone 1, DQ04 and DQ07): the prudent choice, a full truck and the longest distance."""

from __future__ import annotations

import logging

import pandas as pd

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
    register.add("DQ04", "schedule", "warning", int(needs.sum()), f"{column} imputed (full truck: {capacity:g})")
    return result


def supplier_distances(schedule: pd.DataFrame, distances: pd.DataFrame, register: AnomalyRegister) -> pd.DataFrame:
    """DQ07 (H06): distance of every supplier of the planning; a missing one gets the longest planning distance.

    Returns one row per supplier: `supplier_id`, `distance`, `distance_imputed`.
    """
    suppliers = pd.DataFrame({"supplier_id": sorted(schedule["supplier_id"].unique())})
    result = suppliers.merge(distances[["supplier_id", "distance"]], on="supplier_id", how="left")
    if result["distance"].isna().all():
        raise ValueError("distances: no supplier of the planning has a known distance")
    fallback = float(result["distance"].max())
    result["distance_imputed"] = result["distance"].isna()
    result["distance"] = result["distance"].fillna(fallback)
    missing = result.loc[result["distance_imputed"], "supplier_id"].tolist()
    register.add("DQ07", "distances", "warning", len(missing),
                 f"{len(missing)} supplier(s) imputed with the maximum of the planning distances ({fallback} km)")
    logger.info("Fallback distance: %s km, suppliers: %s", fallback, missing)
    return result
