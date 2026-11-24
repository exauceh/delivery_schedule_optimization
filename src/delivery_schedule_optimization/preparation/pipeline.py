"""Preparation pipeline: raw planning and distances -> consolidated dataset (Milestone 1, same steps and order)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from ..domain.config import PlannerConfig, PreparationConfig
from .cleaning import KEY_COLUMNS, check_unique_needs, clean_distances, drop_larger_than_truck, drop_tail, drop_without_need, flag_non_working_days
from .imputation import impute_full_truck, supplier_distances
from .quality import AnomalyRegister, accuracy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedData:
    """Result of the preparation: the consolidated dataset, the anomaly register and the accuracy of the data."""

    consolidated: pd.DataFrame
    anomalies: pd.DataFrame
    accuracy: dict[str, float]


def prepare(schedule_raw: pd.DataFrame, distances_raw: pd.DataFrame, planner: PlannerConfig, config: PreparationConfig) -> PreparedData:
    """Clean the raw planning, complete the missing values and join the distances.

    Steps (Milestone 1): DQ01 needs only, DQ03 cumulative tail, DQ02 flags, DQ04 imputation (full truck),
    DQ05 lines larger than a truck, DQ06 / DQ07 distances. The consolidated dataset keeps the line identity,
    the quantity, the load and the distance.
    """
    register = AnomalyRegister()
    schedule = schedule_raw.drop(columns=["transmission_date", "version_date"], errors="ignore").copy()
    schedule = drop_without_need(schedule, register)
    schedule = drop_tail(schedule, config, register)
    flag_non_working_days(schedule, planner, register)  # after DQ03: the cumulative date is not a delivery day
    schedule = impute_full_truck(schedule, "weight", planner.truck_kg, register)
    schedule = impute_full_truck(schedule, "volume", planner.truck_m3, register)
    schedule = drop_larger_than_truck(schedule, planner, register)
    check_unique_needs(schedule)
    distances = supplier_distances(schedule, clean_distances(distances_raw, register), register)
    schedule = schedule.merge(distances, on="supplier_id", how="left", validate="m:1")

    quality = accuracy(schedule)
    quality["rows_removed"] = float(len(schedule_raw) - len(schedule))
    consolidated = schedule[[*KEY_COLUMNS, "schedule_quantity", "weight", "volume", "distance"]].reset_index(drop=True)
    if consolidated.empty:
        raise ValueError("released schedule: no line left after the preparation")
    logger.info("Raw: %d rows -> consolidated: %d rows (accuracy: rows %.1f %%, weight %.1f %%)", len(schedule_raw), len(consolidated),
                100 * quality["rows_accuracy"], 100 * quality["weight_accuracy"])
    return PreparedData(consolidated=consolidated, anomalies=register.to_frame(), accuracy=quality)
