"""Engine lines: the consolidated dataset checked, sorted and numbered."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..domain.config import PlannerConfig

CONSOLIDATED_COLUMNS = ["date", "supplier_id", "material", "plant", "unloading_point", "schedule_quantity", "weight", "volume", "distance"]


def prepare_lines(consolidated: pd.DataFrame, config: PlannerConfig) -> pd.DataFrame:
    """Turn the consolidated dataset (one row = one need) into the engine lines.

    Checks that every load and distance is resolved, sorts by couple and date, numbers the lines (`line_id`)
    and flags the lines completed with a full truck by the preparation (`imputed_full_truck`).
    """
    lines = consolidated.drop(columns="rate_cost", errors="ignore").copy()
    if missing := set(CONSOLIDATED_COLUMNS).difference(lines.columns):
        raise ValueError(f"consolidated dataset misses columns: {sorted(missing)}")
    lines["date"] = pd.to_datetime(lines["date"])
    if lines[["weight", "volume", "distance"]].isna().any().any() or (lines[["weight", "volume"]] <= 0).any().any():
        raise ValueError("unresolved or non-positive load / distance")
    if ((lines["weight"] > config.truck_kg) | (lines["volume"] > config.truck_m3)).any():
        raise ValueError("a single line is larger than one truck")
    if lines.groupby("supplier_id")["distance"].nunique().gt(1).any():
        raise ValueError("a supplier has several distances")
    lines = lines.sort_values(["supplier_id", "plant", "date", "unloading_point", "material"]).reset_index(drop=True)
    lines.insert(0, "line_id", np.arange(len(lines)))
    # Lines completed with a full truck by the preparation step (weight or volume unknown)
    lines["imputed_full_truck"] = (lines["weight"] == config.truck_kg) | (lines["volume"] == config.truck_m3)
    return lines


def load_lines(path: Path, config: PlannerConfig) -> pd.DataFrame:
    """Load the consolidated dataset from a CSV file and prepare it with `prepare_lines`."""
    return prepare_lines(pd.read_csv(path, parse_dates=["date"]), config)
