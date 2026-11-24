"""Cleaning of the raw planning and of the distances (Milestone 1, rules DQ01, DQ02, DQ03, DQ05 and DQ06)."""

from __future__ import annotations

import logging

import holidays as holiday_calendars
import numpy as np
import pandas as pd

from ..domain.config import PlannerConfig, PreparationConfig
from .quality import AnomalyRegister

logger = logging.getLogger(__name__)

KEY_COLUMNS = ["date", "supplier_id", "material", "plant", "unloading_point"]


def drop_without_need(schedule: pd.DataFrame, register: AnomalyRegister) -> pd.DataFrame:
    """DQ01: a quantity of 0 means no need; negative or missing quantities are errors. All are dropped."""
    qty = schedule["schedule_quantity"]
    register.add("DQ01", "schedule", "info", int((qty == 0).sum()), "dropped (quantity = 0, no need)")
    register.add("DQ01", "schedule", "error", int((qty < 0).sum()), "dropped (negative quantity)")
    register.add("DQ01", "schedule", "error", int(qty.isna().sum()), "dropped (missing quantity)")
    return schedule.loc[qty > 0].copy()


def flag_non_working_days(schedule: pd.DataFrame, config: PlannerConfig, register: AnomalyRegister) -> None:
    """DQ02: needs dated on a weekend or a public holiday are kept and flagged; the engine delivers them before."""
    weekend = schedule["date"].dt.day_of_week >= 5
    years = range(int(schedule["date"].dt.year.min()), int(schedule["date"].dt.year.max()) + 1) if len(schedule) else range(0)
    calendar = holiday_calendars.country_holidays(config.holiday_country, years=years)
    on_holiday = schedule["date"].dt.date.isin(list(calendar))
    register.add("DQ02", "schedule", "warning", int(weekend.sum()), "flagged and kept (no delivery on weekends, handled by the optimization)")
    register.add("DQ02", "schedule", "warning", int(on_holiday.sum()),
                 f"flagged and kept (no delivery on {config.holiday_country} public holidays, handled by the optimization)")


def tail_date(schedule: pd.DataFrame, config: PreparationConfig) -> pd.Timestamp | None:
    """Date from which the planning only accumulates the needs after the daily horizon (see `PreparationConfig`)."""
    if config.tail_date is not None:
        return pd.Timestamp(config.tail_date) if config.tail_date else None
    dates = np.sort(schedule["date"].dropna().unique())
    if len(dates) < 2:
        return None
    last, before = pd.Timestamp(dates[-1]), pd.Timestamp(dates[-2])
    return last if (last - before).days > config.tail_gap_days else None


def drop_tail(schedule: pd.DataFrame, config: PreparationConfig, register: AnomalyRegister) -> pd.DataFrame:
    """DQ03: rows dated on or after the tail date are a cumulative need, not a daily one: they are dropped."""
    tail = tail_date(schedule, config)
    if tail is None:
        register.add("DQ03", "schedule", "info", 0, "no cumulative date at the end of the planning")
        return schedule
    is_tail = schedule["date"] >= tail
    register.add("DQ03", "schedule", "info", int(is_tail.sum()), f"dropped (date >= {tail.date()}, needs accumulated after the horizon)")
    return schedule.loc[~is_tail].copy()


def drop_larger_than_truck(schedule: pd.DataFrame, config: PlannerConfig, register: AnomalyRegister) -> pd.DataFrame:
    """DQ05: a line heavier or bulkier than one truck cannot be delivered whole (RG04): it is dropped."""
    too_large = (schedule["weight"] > config.truck_kg) | (schedule["volume"] > config.truck_m3)
    register.add("DQ05", "schedule", "warning", int(too_large.sum()),
                 f"dropped (weight > {config.truck_kg:.0f} kg or volume > {config.truck_m3} m3, larger than one truck)")
    return schedule.loc[~too_large].copy()


def check_unique_needs(schedule: pd.DataFrame) -> None:
    """A need is identified by its date, supplier, material, plant and dock: a duplicate cannot be resolved safely."""
    duplicated = schedule.duplicated(KEY_COLUMNS, keep=False)
    if duplicated.any():
        raise ValueError(f"released schedule: {int(duplicated.sum())} rows share the same need key {KEY_COLUMNS}")


def clean_distances(distances: pd.DataFrame, register: AnomalyRegister) -> pd.DataFrame:
    """DQ06: a supplier listed twice keeps its first distance; a non-numeric or non-positive distance is missing."""
    register.add("DQ06", "distances", "warning", int(distances["supplier_id"].duplicated().sum()), "kept first value")
    out = distances.drop_duplicates("supplier_id", keep="first").copy()
    out["distance"] = pd.to_numeric(out["distance"], errors="coerce")
    out.loc[out["distance"] <= 0, "distance"] = np.nan
    return out
