from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BaselineConfig:
    truck_kg: float = 24_500.0
    truck_m3: float = 110.4
    margin: float = 2.3923
    diesel_surcharge: float = 1.1943
    pareto_share_a: float = 0.80
    pareto_share_b: float = 0.95

    @property
    def price_factor(self) -> float:
        return (1 + self.margin) * self.diesel_surcharge


def _bound(value: object) -> float:
    text = str(value).strip()
    return float("inf") if text.startswith(">") else float(text)


def load_rate_grid(path: Path) -> pd.DataFrame:
    """Load the canonical wide rate sheet stored in docs/rate_sheet.csv."""
    raw = pd.read_csv(path)
    if "km_up_to" not in raw.columns:
        raise ValueError("rate sheet must contain km_up_to")
    long = raw.melt(id_vars="km_up_to", var_name="kg_max", value_name="base_cost")
    long["km_max"] = long.pop("km_up_to").map(_bound)
    long["kg_max"] = long["kg_max"].map(_bound)
    long["base_cost"] = pd.to_numeric(long["base_cost"], errors="raise")
    grid = long.pivot(index="km_max", columns="kg_max", values="base_cost").sort_index().sort_index(axis=1)
    if grid.isna().any().any():
        raise ValueError("rate sheet contains a missing price")
    return grid


def load_prepared_lines(path: Path) -> pd.DataFrame:
    """Load main's prepared rows while retaining its imputation lineage."""
    lines = pd.read_csv(path, parse_dates=["date"])
    required = {
        "date", "supplier_id", "material", "plant", "unloading_point",
        "schedule_quantity", "weight", "volume", "distance",
        "weight_imputed", "volume_imputed", "distance_imputed",
    }
    missing = required.difference(lines.columns)
    if missing:
        raise ValueError(f"prepared schedule misses columns: {sorted(missing)}")
    for column in ("weight_imputed", "volume_imputed", "distance_imputed"):
        if lines[column].dtype != bool:
            normalized = lines[column].astype(str).str.strip().str.lower().map({"true": True, "false": False})
            if normalized.isna().any():
                raise ValueError(f"{column} contains a value other than true/false")
            lines[column] = normalized
    if lines[["weight", "volume", "distance"]].isna().any().any():
        raise ValueError("prepared schedule contains an unresolved load or distance")
    if (lines[["weight", "volume", "distance"]] <= 0).any().any():
        raise ValueError("prepared schedule contains a non-positive load or distance")
    lines["load_imputed"] = lines["weight_imputed"] | lines["volume_imputed"]
    lines["any_imputed"] = lines["load_imputed"] | lines["distance_imputed"]
    keys = ["date", "supplier_id", "material", "plant", "unloading_point"]
    lines["prepared_line_id"] = lines[keys].astype(str).agg("|".join, axis=1)
    if lines["prepared_line_id"].duplicated().any():
        raise ValueError("prepared line key is not unique")
    return lines


def shipment_cost(grid: pd.DataFrame, distance_km, weight_kg, trucks, config: BaselineConfig) -> np.ndarray:
    trucks_array = np.maximum(np.asarray(trucks, dtype=int), 1)
    distance = np.asarray(distance_km, dtype=float)
    weight_per_truck = np.asarray(weight_kg, dtype=float) / trucks_array
    km_bounds = grid.index.to_numpy(dtype=float)
    kg_bounds = grid.columns.to_numpy(dtype=float)
    km_pos = np.searchsorted(km_bounds, distance, side="left")
    kg_pos = np.searchsorted(kg_bounds, weight_per_truck, side="left")
    if (km_pos >= len(km_bounds)).any() or (kg_pos >= len(kg_bounds)).any():
        raise ValueError("load or distance is outside the rate sheet")
    return grid.to_numpy()[km_pos, kg_pos] * config.price_factor * trucks_array


def _first_fit_count(group: pd.DataFrame, config: BaselineConfig) -> int:
    ordered = group.assign(
        _score=np.maximum(group["weight"] / config.truck_kg, group["volume"] / config.truck_m3)
    ).sort_values("_score", ascending=False)
    bins: list[list[float]] = []
    for row in ordered.itertuples():
        for load in bins:
            if load[0] + row.weight <= config.truck_kg and load[1] + row.volume <= config.truck_m3:
                load[0] += row.weight
                load[1] += row.volume
                break
        else:
            bins.append([row.weight, row.volume])
    return len(bins)


def build_shipments(
    lines: pd.DataFrame,
    grid: pd.DataFrame,
    config: BaselineConfig,
    period: str,
) -> pd.DataFrame:
    """Build a reconstructed daily baseline or a weekly consolidation benchmark."""
    data = lines.copy()
    if period == "day":
        data["period"] = data["date"]
    elif period == "week":
        data["period"] = data["date"] - pd.to_timedelta(data["date"].dt.day_of_week, unit="D")
    else:
        raise ValueError("period must be 'day' or 'week'")
    keys = ["supplier_id", "plant", "period"]
    shipments = (
        data.groupby(keys, sort=True)
        .agg(
            lines=("prepared_line_id", "size"),
            materials=("material", "nunique"),
            unloading_points=("unloading_point", "nunique"),
            weight=("weight", "sum"),
            volume=("volume", "sum"),
            distance=("distance", "first"),
            imputed_rows=("any_imputed", "sum"),
            load_imputed_rows=("load_imputed", "sum"),
            distance_imputed=("distance_imputed", "max"),
        )
        .reset_index()
    )
    shipments["weight_utilization"] = shipments["weight"] / config.truck_kg
    shipments["volume_utilization"] = shipments["volume"] / config.truck_m3
    shipments["dimensioning_utilization"] = shipments[["weight_utilization", "volume_utilization"]].max(axis=1)
    shipments["trucks_lower_bound"] = np.ceil(shipments["dimensioning_utilization"]).astype(int)
    packed = data.groupby(keys, sort=True).apply(lambda group: _first_fit_count(group, config), include_groups=False)
    shipments["trucks_packed"] = packed.to_numpy(dtype=int)
    if (shipments["trucks_packed"] < shipments["trucks_lower_bound"]).any():
        raise AssertionError("packing uses fewer trucks than the capacity lower bound")
    shipments["cost"] = shipment_cost(
        grid, shipments["distance"], shipments["weight"], shipments["trucks_packed"], config
    )
    return shipments


def summarize_scope(name: str, lines: pd.DataFrame, grid: pd.DataFrame, config: BaselineConfig) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    daily = build_shipments(lines, grid, config, "day")
    weekly = build_shipments(lines, grid, config, "week")
    current_cost = float(daily["cost"].sum())
    weekly_cost = float(weekly["cost"].sum())
    summary = {
        "scope": name,
        "lines": len(lines),
        "suppliers": int(lines["supplier_id"].nunique()),
        "couples": int(lines.groupby(["supplier_id", "plant"]).ngroups),
        "daily_shipments": len(daily),
        "daily_trucks": int(daily["trucks_packed"].sum()),
        "current_cost_eur": current_cost,
        "weekly_shipments": len(weekly),
        "weekly_trucks": int(weekly["trucks_packed"].sum()),
        "weekly_cost_eur": weekly_cost,
        "potential_eur": current_cost - weekly_cost,
        "potential_pct": 100 * (current_cost - weekly_cost) / current_cost,
        "imputed_rows": int(lines["any_imputed"].sum()),
        "packing_extra_trucks": int((daily["trucks_packed"] - daily["trucks_lower_bound"]).sum()),
    }
    return summary, daily, weekly


def rank_couples(lines: pd.DataFrame, daily: pd.DataFrame, weekly: pd.DataFrame, config: BaselineConfig) -> pd.DataFrame:
    keys = ["supplier_id", "plant"]
    current = daily.groupby(keys).agg(current_cost=("cost", "sum"), deliveries=("period", "size"))
    scenario = weekly.groupby(keys).agg(weekly_cost=("cost", "sum"), weekly_departures=("period", "size"), weekly_trucks=("trucks_packed", "sum"))
    reliability = lines.groupby(keys).agg(
        source_lines=("prepared_line_id", "size"),
        imputed_rows=("any_imputed", "sum"),
        load_imputed_rows=("load_imputed", "sum"),
        distance_imputed=("distance_imputed", "max"),
    )
    ranking = current.join(scenario).join(reliability)
    ranking["potential_eur"] = ranking["current_cost"] - ranking["weekly_cost"]
    ranking["potential_pct"] = 100 * ranking["potential_eur"] / ranking["current_cost"]
    ranking["reliability"] = np.select(
        [ranking["load_imputed_rows"] > 0, ranking["distance_imputed"]],
        ["load_imputed", "distance_imputed"],
        default="measured",
    )
    ranking = ranking.sort_values("potential_eur", ascending=False)
    total = ranking["potential_eur"].sum()
    ranking["rank"] = np.arange(1, len(ranking) + 1)
    ranking["cumulative_share"] = ranking["potential_eur"].cumsum() / total
    before = ranking["cumulative_share"].shift(fill_value=0)
    ranking["potential_class"] = np.select(
        [before < config.pareto_share_a, before < config.pareto_share_b], ["A", "B"], default="C"
    )
    return ranking.reset_index()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_testbed(root: Path, output_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    root = root.resolve()
    output_dir = output_dir or root / "results" / "milestone2_testbed"
    output_dir.mkdir(parents=True, exist_ok=True)
    schedule_path = root / "data" / "schedule_clean.csv"
    rate_path = root / "docs" / "rate_sheet.csv"
    config = BaselineConfig()
    lines = load_prepared_lines(schedule_path)
    grid = load_rate_grid(rate_path)

    scopes = {
        "prepared_full": lines,
        "measured_load": lines.loc[~lines["load_imputed"]].copy(),
        "measured_and_priced": lines.loc[~lines["any_imputed"]].copy(),
    }
    summaries: list[dict] = []
    detailed: dict[str, pd.DataFrame] = {}
    for name, scoped_lines in scopes.items():
        summary, daily, weekly = summarize_scope(name, scoped_lines, grid, config)
        summaries.append(summary)
        detailed[f"daily_{name}"] = daily
        detailed[f"weekly_{name}"] = weekly

    comparison = pd.DataFrame(summaries)
    full_daily = detailed["daily_prepared_full"]
    full_weekly = detailed["weekly_prepared_full"]
    ranking = rank_couples(lines, full_daily, full_weekly, config)

    comparison.to_csv(output_dir / "scope_comparison.csv", index=False)
    full_daily.to_csv(output_dir / "baseline_reconstructed_prepared.csv", index=False)
    full_weekly.to_csv(output_dir / "weekly_benchmark_prepared.csv", index=False)
    ranking.to_csv(output_dir / "couple_ranking_with_reliability.csv", index=False)
    manifest = {
        "method": "reconstructed demand-date baseline and weekly consolidation benchmark",
        "config": asdict(config),
        "inputs": {
            "schedule_clean.csv": _sha256(schedule_path),
            "rate_sheet.csv": _sha256(rate_path),
        },
        "reservations": [
            "Deliveries are reconstructed from demand dates, not observed departures.",
            "prepared_full includes imputed loads and one imputed supplier distance.",
            "The weekly benchmark is not a dated operating plan.",
        ],
    }
    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"comparison": comparison, "ranking": ranking, **detailed}
