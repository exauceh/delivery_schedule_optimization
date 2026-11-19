from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .baseline import BaselineConfig, load_prepared_lines, load_rate_grid, run_testbed, shipment_cost


WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri")


def _easter_sunday(year: int) -> date:
    """Gregorian Easter date, used only for the German national calendar fallback."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def german_national_holidays(years: set[int]) -> set[date]:
    result: set[date] = set()
    for year in years:
        easter = _easter_sunday(year)
        result.update(
            {
                date(year, 1, 1),
                easter - timedelta(days=2),
                easter + timedelta(days=1),
                date(year, 5, 1),
                easter + timedelta(days=39),
                easter + timedelta(days=50),
                date(year, 10, 3),
                date(year, 12, 25),
                date(year, 12, 26),
            }
        )
    return result


def candidate_calendars() -> list[tuple[int, ...]]:
    return [combo for size in range(1, 6) for combo in itertools.combinations(range(5), size)]


def _latest_allowed_date(due: pd.Timestamp, weekdays: tuple[int, ...], holidays: set[date], max_advance: int) -> pd.Timestamp | None:
    for days_early in range(max_advance + 1):
        candidate = due - pd.Timedelta(days=days_early)
        if candidate.day_of_week in weekdays and candidate.date() not in holidays:
            return candidate.normalize()
    return None


@dataclass
class PackedTruck:
    weight: float
    volume: float
    line_ids: list[str]


def _pack_arrays(
    weights: np.ndarray,
    volumes: np.ndarray,
    line_ids: np.ndarray,
    config: BaselineConfig,
) -> list[PackedTruck]:
    scores = np.maximum(weights / config.truck_kg, volumes / config.truck_m3)
    order = np.lexsort((-volumes, -weights, -scores))
    trucks: list[PackedTruck] = []
    for index in order:
        weight = float(weights[index])
        volume = float(volumes[index])
        line_id = str(line_ids[index])
        if weight > config.truck_kg or volume > config.truck_m3:
            raise ValueError(f"line {line_id} exceeds truck capacity")
        for truck in trucks:
            if truck.weight + weight <= config.truck_kg and truck.volume + volume <= config.truck_m3:
                truck.weight += weight
                truck.volume += volume
                truck.line_ids.append(line_id)
                break
        else:
            trucks.append(PackedTruck(weight, volume, [line_id]))
    return trucks


def _evaluate_calendar(
    lines: pd.DataFrame,
    grid: pd.DataFrame,
    config: BaselineConfig,
    weekdays: tuple[int, ...],
    holidays: set[date],
    max_advance: int,
) -> dict:
    due_dates = lines["date"].to_numpy(dtype="datetime64[D]")
    departures = np.array(
        [
            np.datetime64(value.date(), "D") if value is not None else np.datetime64("NaT")
            for value in (_latest_allowed_date(due, weekdays, holidays, max_advance) for due in lines["date"])
        ]
    )
    if np.isnat(departures).any():
        return {"feasible": False, "reason": "no allowed departure within maximum anticipation"}
    anticipation = (due_dates - departures).astype("timedelta64[D]").astype(int)
    weights = lines["weight"].to_numpy(dtype=float)
    volumes = lines["volume"].to_numpy(dtype=float)
    line_ids = lines["prepared_line_id"].to_numpy()
    distances = lines["distance"].unique()
    if len(distances) != 1:
        raise ValueError("one supplier has inconsistent distances")
    distance = float(distances[0])
    total_cost = 0.0
    total_trucks = 0
    trucks_per_departure: list[int] = []
    weight_capacity = 0.0
    volume_capacity = 0.0
    unique_departures, departure_groups = np.unique(departures, return_inverse=True)
    for group_index in range(len(unique_departures)):
        selected = departure_groups == group_index
        packed = _pack_arrays(weights[selected], volumes[selected], line_ids[selected], config)
        total_trucks += len(packed)
        trucks_per_departure.append(len(packed))
        weight_capacity += len(packed) * config.truck_kg
        volume_capacity += len(packed) * config.truck_m3
        truck_weights = np.array([truck.weight for truck in packed])
        total_cost += float(
            shipment_cost(grid, np.full(len(packed), distance), truck_weights, np.ones(len(packed)), config).sum()
        )
    return {
        "feasible": True,
        "reason": "",
        "frequency": len(weekdays),
        "weekdays": "+".join(WEEKDAY_NAMES[index] for index in weekdays),
        "departures": len(unique_departures),
        "trucks": total_trucks,
        "max_trucks_per_departure": max(trucks_per_departure),
        "multi_truck_departures": sum(value > 1 for value in trucks_per_departure),
        "cost_eur": total_cost,
        "weight_utilization_pct": 100 * float(weights.sum()) / weight_capacity,
        "volume_utilization_pct": 100 * float(volumes.sum()) / volume_capacity,
        "mean_anticipation_days": float(anticipation.mean()),
        "max_anticipation_days": int(anticipation.max()),
    }


def optimize_profiles(
    lines: pd.DataFrame,
    grid: pd.DataFrame,
    group_columns: list[str],
    scenario: str,
    config: BaselineConfig | None = None,
    max_advance: int = 7,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    config = config or BaselineConfig()
    years = set(range(lines["date"].dt.year.min() - 1, lines["date"].dt.year.max() + 1))
    holidays = german_national_holidays(years)
    candidates: list[dict] = []
    selected: list[dict] = []
    calendars = candidate_calendars()

    for group_key, group in lines.groupby(group_columns, sort=True):
        values = group_key if isinstance(group_key, tuple) else (group_key,)
        identity = dict(zip(group_columns, values))
        group_candidates: list[dict] = []
        for weekdays in calendars:
            result = _evaluate_calendar(group, grid, config, weekdays, holidays, max_advance)
            record = {"scenario": scenario, **identity, **result}
            group_candidates.append(record)
            candidates.append(record)
        feasible = [record for record in group_candidates if record["feasible"]]
        if not feasible:
            selected.append({"scenario": scenario, **identity, "feasible": False})
            continue
        winner = min(feasible, key=_cost_key)
        selected.append(winner.copy())
    return pd.DataFrame(candidates), pd.DataFrame(selected)


def _cost_key(record: dict | pd.Series) -> tuple:
    return (
        round(float(record["cost_eur"]), 8),
        int(record["trucks"]),
        int(record["frequency"]),
        float(record["mean_anticipation_days"]),
        str(record["weekdays"]),
    )


def _spread_key(record: dict | pd.Series) -> tuple:
    return (
        int(record["max_trucks_per_departure"]),
        int(record["multi_truck_departures"]),
        round(float(record["cost_eur"]), 8),
        int(record["trucks"]),
        int(record["frequency"]),
        float(record["mean_anticipation_days"]),
        str(record["weekdays"]),
    )


def select_profiles(candidates: pd.DataFrame, group_columns: list[str], policy: str) -> pd.DataFrame:
    key = _cost_key if policy == "cost_min" else _spread_key if policy == "spread_before_extra_truck" else None
    if key is None:
        raise ValueError("unknown profile selection policy")
    selected: list[pd.Series] = []
    for _, group in candidates.loc[candidates["feasible"]].groupby(group_columns, sort=True):
        winner_index = min(group.index, key=lambda index: key(group.loc[index]))
        winner = group.loc[winner_index].copy()
        winner["policy"] = policy
        selected.append(winner)
    return pd.DataFrame(selected).reset_index(drop=True)


def _scenario_summary(scope: str, scenario: str, selected: pd.DataFrame) -> dict:
    feasible = bool(selected["feasible"].all())
    return {
        "scope": scope,
        "scenario": scenario,
        "groups": len(selected),
        "feasible": feasible,
        "trucks": int(selected["trucks"].sum()) if feasible else np.nan,
        "cost_eur": float(selected["cost_eur"].sum()) if feasible else np.nan,
        "mean_weight_utilization_pct": float(selected["weight_utilization_pct"].mean()) if feasible else np.nan,
        "mean_volume_utilization_pct": float(selected["volume_utilization_pct"].mean()) if feasible else np.nan,
        "mean_anticipation_days": float(selected["mean_anticipation_days"].mean()) if feasible else np.nan,
        "max_anticipation_days": int(selected["max_anticipation_days"].max()) if feasible else np.nan,
        "max_trucks_per_departure": int(selected["max_trucks_per_departure"].max()) if feasible else np.nan,
        "multi_truck_departures": int(selected["multi_truck_departures"].sum()) if feasible else np.nan,
    }


def run_profile_testbed(root: Path, output_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    root = root.resolve()
    output_dir = output_dir or root / "results" / "profile_testbed"
    output_dir.mkdir(parents=True, exist_ok=True)
    config = BaselineConfig()
    lines = load_prepared_lines(root / "data" / "schedule_clean.csv")
    grid = load_rate_grid(root / "docs" / "rate_sheet.csv")
    baseline_results = run_testbed(root)
    baseline = baseline_results["comparison"].set_index("scope")
    scopes = {
        "prepared_full": lines,
        "measured_and_priced": lines.loc[~lines["any_imputed"]].copy(),
    }
    summaries: list[dict] = []
    all_selected: list[pd.DataFrame] = []
    all_candidates: list[pd.DataFrame] = []

    for scope, scoped_lines in scopes.items():
        baseline_daily = baseline_results[f"daily_{scope}"]
        summaries.append(
            {
                "scope": scope,
                "scenario": "A_reconstructed_daily",
                "groups": int(baseline.loc[scope, "couples"]),
                "feasible": True,
                "trucks": int(baseline.loc[scope, "daily_trucks"]),
                "cost_eur": float(baseline.loc[scope, "current_cost_eur"]),
                "mean_weight_utilization_pct": np.nan,
                "mean_volume_utilization_pct": np.nan,
                "mean_anticipation_days": 0.0,
                "max_anticipation_days": 0,
                "max_trucks_per_departure": int(baseline_daily["trucks_packed"].max()),
                "multi_truck_departures": int((baseline_daily["trucks_packed"] > 1).sum()),
            }
        )
        for scenario, group_columns in (
            ("B_supplier_plant", ["supplier_id", "plant"]),
            ("C_supplier_hub", ["supplier_id"]),
        ):
            candidates, _ = optimize_profiles(scoped_lines, grid, group_columns, scenario, config)
            candidates.insert(0, "scope", scope)
            all_candidates.append(candidates)
            for policy in ("cost_min", "spread_before_extra_truck"):
                selected = select_profiles(candidates, group_columns, policy)
                selected["scenario"] = f"{scenario}_{policy}"
                summaries.append(_scenario_summary(scope, f"{scenario}_{policy}", selected))
                all_selected.append(selected)

    comparison = pd.DataFrame(summaries)
    for scope, group in comparison.groupby("scope"):
        baseline_cost = float(group.loc[group["scenario"] == "A_reconstructed_daily", "cost_eur"].iloc[0])
        comparison.loc[group.index, "saving_vs_A_eur"] = baseline_cost - comparison.loc[group.index, "cost_eur"]
        comparison.loc[group.index, "saving_vs_A_pct"] = 100 * comparison.loc[group.index, "saving_vs_A_eur"] / baseline_cost
    selected_profiles = pd.concat(all_selected, ignore_index=True)
    candidate_results = pd.concat(all_candidates, ignore_index=True)
    comparison.to_csv(output_dir / "scenario_comparison.csv", index=False)
    selected_profiles.to_csv(output_dir / "selected_profiles.csv", index=False)
    candidate_results.to_csv(output_dir / "candidate_profiles.csv", index=False)
    return {
        "comparison": comparison,
        "selected_profiles": selected_profiles,
        "candidate_profiles": candidate_results,
    }
