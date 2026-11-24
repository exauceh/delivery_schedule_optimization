"""Current situation (reference A): one delivery per couple and need day, loaded truck by truck like the plan."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ..domain.config import COUPLE, PlannerConfig
from ..domain.models import Plans
from ..engine.loading import pack
from ..engine.plans import plan_couple
from ..pricing.tariff import Tariff


def load_baseline(path: Path, lines: pd.DataFrame) -> pd.DataFrame:
    """Reference A computed in Milestone 2 (baseline_deliveries_<imputation>.csv): one row per current delivery.

    Milestone 2 rule (RG12, RG11): one delivery per couple and need day; trucks = ceil(max(weight / 24.5 t,
    volume / 110.4 m3)); full trucks first, the rest in the last truck, each truck priced on its own weight.
    """
    baseline = pd.read_csv(path, parse_dates=["date"])
    required = {"supplier_id", "plant", "date", "weight", "volume", "distance", "trucks", "fill", "cost"}
    if missing := required.difference(baseline.columns):
        raise ValueError(f"Milestone 2 baseline misses columns: {sorted(missing)}")
    same_couples = set(map(tuple, baseline[COUPLE].drop_duplicates().to_numpy())) == set(map(tuple, lines[COUPLE].drop_duplicates().to_numpy()))
    if not same_couples or not np.isclose(baseline["weight"].sum(), lines["weight"].sum()):
        raise ValueError("Milestone 2 baseline does not come from the same consolidated dataset")
    return baseline


def current_situation(lines: pd.DataFrame, baseline: pd.DataFrame, tariff: Tariff,
                      config: PlannerConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reference A loaded truck by truck, like the plan (same reference as the `main` branch).

    The current deliveries are those of Milestone 2 (one per couple and need day, RG12), but their lines are
    loaded whole with `pack` and each truck is priced on its own weight, exactly like the optimised plan:
    both sides of the saving are measured the same way. (The Milestone 2 estimator splits the load
    continuously and rounds the number of trucks up, which counts 3 trucks too many when a delivery is
    exactly 3 full trucks, because of floating-point sums.)

    Returns the deliveries (Milestone 2 columns, `trucks`, `fill` and `cost` recomputed, the Milestone 2
    figures kept in `estimator_trucks` and `estimator_cost`) and one row per truck.
    """
    deliveries, trucks = current_deliveries(lines, tariff, config)
    loaded = deliveries[["supplier_id", "plant", "date", "trucks", "cost"]]
    out = baseline.rename(columns={"trucks": "estimator_trucks", "cost": "estimator_cost"}).merge(
        loaded, on=["supplier_id", "plant", "date"], how="outer", validate="1:1", indicator=True)
    if not (out.pop("_merge") == "both").all():
        raise ValueError("the Milestone 2 deliveries and the dataset lines do not match")
    return out, trucks


def current_deliveries(lines: pd.DataFrame, tariff: Tariff, config: PlannerConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reference A computed from the lines alone: one delivery per couple and need day (RG12).

    Each delivery is loaded whole lines first fit decreasing (`pack`) and each truck is priced on its own
    weight, exactly like the optimised plan, so that both sides of the saving are measured the same way.
    Returns one row per delivery (`lines`, `weight`, `volume`, `distance`, `trucks`, `cost`) and one row per truck.
    """
    rows: list[dict[str, object]] = []
    trucks: list[dict[str, object]] = []
    for (supplier, plant, day), group in lines.groupby(["supplier_id", "plant", "date"], sort=True):
        weight, volume = group["weight"].to_numpy(float), group["volume"].to_numpy(float)
        packed = pack(weight, volume, config)
        kg = np.array([weight[t].sum() for t in packed])
        m3 = np.array([volume[t].sum() for t in packed])
        distance = float(group["distance"].iloc[0])
        prices = tariff(np.full(len(packed), distance), kg)
        rows.append({"supplier_id": supplier, "plant": plant, "date": day, "lines": len(group), "weight": float(weight.sum()),
                     "volume": float(volume.sum()), "distance": distance, "trucks": len(packed), "cost": float(prices.sum())})
        trucks += [{"supplier_id": supplier, "plant": plant, "date": day, "distance": distance, "weight": w, "volume": v,
                    "fill": max(w / config.truck_kg, v / config.truck_m3), "cost": float(c)}
                   for w, v, c in zip(kg, m3, prices, strict=True)]
    return pd.DataFrame(rows), pd.DataFrame(trucks)


def reference_plan(lines: pd.DataFrame, holidays: set[date], tariff: Tariff, config: PlannerConfig) -> Plans:
    """Scenario A rebuilt with the planner's packing (used to compare with `prime`, whose reference packs whole lines)."""
    plans: Plans = {}
    for key, group in lines.groupby(COUPLE, sort=True):
        plan = plan_couple(group, tuple(range(5)), holidays, tariff, config, optimize=False)
        if plan is None or (plan.dates != group["date"].to_numpy(dtype="datetime64[D]")).any():
            raise ValueError(f"need outside a working day for {key}")
        plans[(str(key[0]), str(key[1]))] = plan
    return plans
