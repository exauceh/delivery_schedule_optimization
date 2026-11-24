"""Indicators of a plan: load of the plants per day and comparison with the current situation."""

from __future__ import annotations

import pandas as pd


def plant_day_load(table: pd.DataFrame) -> pd.DataFrame:
    """Trucks received per plant and per day (RG09 indicator).

    `table` = one row per truck, or one row per delivery with a `trucks` column (Milestone 2 baseline).
    """
    grouped = table.groupby(["plant", "date"])
    counts = grouped["trucks"].sum() if "trucks" in table.columns else grouped.size()
    return counts.unstack("plant", fill_value=0)


def plan_kpis(lines: pd.DataFrame, deliveries_before: pd.DataFrame, trucks_after: pd.DataFrame, allocations: pd.DataFrame) -> dict[str, float]:
    """Before / after indicators of a run (output 3 summary): deliveries, trucks, cost, saving, fill, peak, advance."""
    cost_before, cost_after = float(deliveries_before["cost"].sum()), float(trucks_after["cost"].sum())
    trucks_before, n_trucks_after = int(deliveries_before["trucks"].sum()), len(trucks_after)
    return {
        "couples": float(lines.groupby(["supplier_id", "plant"]).ngroups),
        "lines": float(len(lines)),
        "deliveries_before": float(len(deliveries_before)),
        "deliveries_after": float(trucks_after.groupby(["supplier_id", "plant", "date"]).ngroups),
        "trucks_before": float(trucks_before),
        "trucks_after": float(n_trucks_after),
        "cost_before": cost_before,
        "cost_after": cost_after,
        "saving": cost_before - cost_after,
        "saving_pct": 100 * (1 - cost_after / cost_before) if cost_before else 0.0,
        "mean_truck_weight_before_kg": float(lines["weight"].sum() / trucks_before) if trucks_before else 0.0,
        "mean_truck_weight_after_kg": float(lines["weight"].sum() / n_trucks_after) if n_trucks_after else 0.0,
        "peak_trucks_per_plant_day_before": float(plant_day_load(deliveries_before).max().max()),
        "peak_trucks_per_plant_day_after": float(plant_day_load(trucks_after).max().max()),
        "mean_advance_days": float(allocations["advance_days"].mean()),
    }
