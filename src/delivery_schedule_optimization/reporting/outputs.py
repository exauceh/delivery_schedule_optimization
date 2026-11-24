"""Outputs of the engine: trucks and allocations (output 2), profiles and savings per couple (outputs 1 and 3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import cast

import numpy as np
import pandas as pd

from ..domain.calendar import horizon_weeks, monday, plan_start
from ..domain.config import COUPLE, DAY_NAMES, PlannerConfig
from ..domain.models import Candidates, Plans
from ..engine.selection import selection_key
from ..pricing.tariff import Tariff


def delivery_rule(slot: date, delivery: pd.Timestamp) -> str:
    """Why a need leaves on that day (EF10)."""
    return "jour du profil" if delivery.date() == slot else "férié avancé"


def export(lines: pd.DataFrame, plans: Plans, scenario: str, tariff: Tariff, config: PlannerConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Trucks (output 2, header) and allocations (output 2, one row per need)."""
    truck_rows: list[dict[str, object]] = []
    alloc_rows: list[dict[str, object]] = []
    groups = dict(tuple(lines.groupby(COUPLE, sort=True)))
    for key, plan in sorted(plans.items()):
        group = groups[key]
        distance = float(group["distance"].iloc[0])
        for (truck_day, positions), kg in zip(plan.trucks, plan.truck_weights):
            truck_id = f"{scenario}-{len(truck_rows) + 1:05d}"
            sub = group.iloc[positions]
            m3 = float(sub["volume"].sum())
            day = pd.Timestamp(truck_day)
            truck_rows.append({
                "truck_id": truck_id, "date": day, "weekday": DAY_NAMES[day.weekday()],
                "supplier_id": key[0], "plant": key[1], "distance": distance,
                "lines": len(sub), "weight": float(kg), "volume": m3,
                "fill_weight": kg / config.truck_kg, "fill_volume": m3 / config.truck_m3,
                "fill": max(kg / config.truck_kg, m3 / config.truck_m3),
                "imputed_full_truck": bool(sub["imputed_full_truck"].any()),
                "cost": float(tariff(distance, kg)),
            })
            for position, row in zip(positions, sub.itertuples(index=False)):
                slot = plan.slots[position]
                alloc_rows.append({
                    "truck_id": truck_id, "delivery_date": day, "need_date": row.date,
                    "advance_days": (cast(pd.Timestamp, row.date) - day).days, "profile_day": pd.Timestamp(slot),
                    "delivery_rule": delivery_rule(slot, day),
                    "line_id": row.line_id, "supplier_id": key[0], "plant": key[1],
                    "unloading_point": row.unloading_point, "material": row.material,
                    "schedule_quantity": row.schedule_quantity, "weight": row.weight, "volume": row.volume,
                    "imputed_full_truck": row.imputed_full_truck,
                })
    return pd.DataFrame(truck_rows), pd.DataFrame(alloc_rows)


def truck_contents(allocations: pd.DataFrame) -> pd.DataFrame:
    """Output 2 in the format of the project brief: per truck, material and dock, the regrouped need days."""
    grouped = allocations.groupby(["truck_id", "delivery_date", "supplier_id", "plant", "unloading_point", "material"])
    return grouped.agg(
        need_days=("need_date", lambda s: ", ".join(sorted({d.strftime("%d/%m") for d in s}))),
        need_lines=("line_id", "size"),
        quantity=("schedule_quantity", "sum"),
        weight=("weight", "sum"),
        volume=("volume", "sum"),
    ).reset_index()


def couple_summary(lines: pd.DataFrame, baseline: pd.DataFrame, plans_B: Plans, candidates: Candidates, unsmoothed: Plans,
                   config: PlannerConfig) -> pd.DataFrame:
    """Outputs 1 and 3: profile, weekly pattern and saving per couple, with the reason of each choice (EF10).

    `baseline` = reference A of Milestone 2, one row per current delivery.
    """
    weeks = horizon_weeks(lines, plan_start(lines, config))  # the plan may deliver the week before the first need
    imputed = lines.groupby(COUPLE)["imputed_full_truck"].sum()
    current = dict(tuple(baseline.groupby(COUPLE, sort=True)))
    def days(profile: tuple[int, ...]) -> str:
        return " + ".join(DAY_NAMES[d] for d in profile)

    rows = []
    for key, plan in sorted(plans_B.items()):
        before, rg07 = current[key], unsmoothed[key]
        before_weeks = pd.Series([monday(d.date()) for d in before["date"]]).value_counts()
        pattern_before = [int(before_weeks.get(w, 0)) for w in weeks]
        pattern_after = [plan.weekly_deliveries.get(w, 0) for w in weeks]
        lower = [p for p in candidates[key] if p.frequency < rg07.frequency]
        if rg07.overflow_departures:
            why = f"profil {rg07.frequency} : des départs dépassent un camion quel que soit le plan, on ajoute des camions"
        else:
            why = f"profil {rg07.frequency} : meilleur plan dont chaque départ tient dans un camion"
            fitting_lower = [p for p in lower if p.overflow_departures == 0]
            if fitting_lower:
                alt = min(fitting_lower, key=lambda p: selection_key(p, config))
                why += f" (profil {alt.frequency} possible : {alt.n_trucks - rg07.n_trucks:+d} camion(s), {alt.cost - rg07.cost:+.0f} EUR)"
            elif lower:
                alt = min(lower, key=lambda p: p.overflow_departures)
                why += (f" ; avec moins de jours, {alt.overflow_departures} départ(s) dépasseraient un camion "
                        f"(le client préfère ajouter des jours plutôt que des camions)")
        if plan.frequency != rg07.frequency:
            why += f" ; lissage : profil {plan.frequency}"
        if plan.profile != rg07.profile:
            why += f" ; lissage : {days(plan.profile)} plutôt que {days(rg07.profile)} ({plan.cost - rg07.cost:+.0f} EUR)"
        rows.append({
            "supplier_id": key[0], "plant": key[1], "distance": float(lines.loc[lines["supplier_id"] == key[0], "distance"].iloc[0]),
            "current_profile": max(pattern_before), "current_weekly_pattern": " ".join(map(str, pattern_before)),
            "profile": plan.frequency, "profile_days": days(plan.profile),
            "weekly_pattern": " ".join(map(str, pattern_after)),
            "deliveries_before": len(before), "deliveries_after": len(np.unique(plan.dates)),
            "trucks_before": int(before["trucks"].sum()), "trucks_after": plan.n_trucks,
            "trucks_per_active_week": plan.n_trucks / max(1, sum(n > 0 for n in pattern_after)),
            "mean_fill_weight_pct": 100 * float(np.mean(plan.truck_weights)) / config.truck_kg,
            "mean_advance_days": plan.total_advance / len(plan.slots),
            "overflow_departures": plan.overflow_departures, "imputed_full_truck_lines": int(imputed[key]),
            "cost_before": float(before["cost"].sum()), "cost_after": plan.cost,
            "saving": float(before["cost"].sum()) - plan.cost, "reason": why,
        })
    out = pd.DataFrame(rows)
    out["saving_pct"] = 100 * out["saving"] / out["cost_before"]
    return out.sort_values("saving", ascending=False).reset_index(drop=True)


PROFILE_COLUMNS = ["supplier_id", "plant", "distance", "current_profile", "current_weekly_pattern", "profile", "profile_days",
                   "weekly_pattern", "trucks_per_active_week", "mean_fill_weight_pct", "mean_advance_days", "reason"]
SAVING_COLUMNS = ["supplier_id", "plant", "distance", "deliveries_before", "deliveries_after", "trucks_before", "trucks_after",
                  "imputed_full_truck_lines", "cost_before", "cost_after", "saving", "saving_pct"]


@dataclass(frozen=True)
class PlanOutputs:
    """The three outputs of the project brief: profiles (1), trucks and their contents (2), savings (3)."""

    profiles: pd.DataFrame
    truck_contents: pd.DataFrame
    trucks: pd.DataFrame
    allocations: pd.DataFrame
    saving: pd.DataFrame


def build_outputs(lines: pd.DataFrame, deliveries_before: pd.DataFrame, plans: Plans, unsmoothed: Plans, candidates: Candidates,
                  tariff: Tariff, config: PlannerConfig) -> PlanOutputs:
    """Outputs 1, 2 and 3 of the retained plan, with the columns of the milestone 3 notebook."""
    trucks, allocations = export(lines, plans, "B", tariff, config)
    summary = couple_summary(lines, deliveries_before, plans, candidates, unsmoothed, config)
    profiles = summary[PROFILE_COLUMNS].sort_values(["plant", "supplier_id"]).reset_index(drop=True)
    saving = summary[SAVING_COLUMNS]
    if not np.isclose(saving["saving"].sum(), deliveries_before["cost"].sum() - trucks["cost"].sum()):
        raise ValueError("output 3: the savings per couple do not add up to the total saving")
    return PlanOutputs(profiles=profiles, truck_contents=truck_contents(allocations), trucks=trucks, allocations=allocations, saving=saving)
