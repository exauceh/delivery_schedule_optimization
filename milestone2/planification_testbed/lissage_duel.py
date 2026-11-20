"""Compare the daily peaks of two interchangeable plans per supplier–plant pair."""
import argparse
import io
import json
from pathlib import Path
import subprocess
from time import perf_counter

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp


KEY = ["supplier_id", "plant"]


def solve(objective, limits, base_constraint, n):
    answer = milp(
        objective, integrality=np.ones(n + 1), bounds=limits,
        constraints=base_constraint, options={"mip_rel_gap": 0.0, "time_limit": 15},
    )
    if answer.status != 0:
        raise RuntimeError(f"Optimality not established: {answer.message}")
    return np.rint(answer.x).astype(int)


def compare(repo: Path, commit: str, duel: Path) -> None:
    start = perf_counter()
    from delivery_schedule_optimization.fast_plan import run

    def blob(path):
        return subprocess.check_output([
            "git", "-c", f"safe.directory={repo.resolve().as_posix()}",
            "-C", str(repo.resolve()), "show", f"{commit}:{path}",
        ])

    def published(name):
        return pd.read_csv(io.BytesIO(blob(f"data/{name}.csv")))

    prepared = published("consolidated_dataset")
    identity = KEY + ["date", "material", "unloading_point"]
    raw = pd.read_csv(io.BytesIO(blob("docs/released_schedule.csv")))
    provenance = prepared[identity].merge(raw[identity + ["weight", "volume"]], on=identity,
                                          how="left", validate="1:1", indicator=True)
    if not provenance["_merge"].eq("both").all():
        raise ValueError("Missing source line for prepared data")
    for column in ("weight", "volume"):
        prepared[f"{column}_imputed"] = provenance[column].fillna(0).le(0)
    distances = pd.read_csv(io.BytesIO(blob("docs/distances.csv")))
    prepared["distance_imputed"] = ~prepared.supplier_id.isin(distances.loc[distances.distance.gt(0), "supplier_id"])
    (duel / "data").mkdir(parents=True, exist_ok=True)
    (duel / "docs").mkdir(exist_ok=True)
    prepared.to_csv(duel / "data/schedule_clean.csv", index=False)
    (duel / "docs/rate_sheet.csv").write_bytes(blob("docs/rate_sheet.csv"))
    run(duel, duel / "heuristic", include_imputed=True)

    base = published("optimized_trucks")
    base_lines = published("optimized_lines")
    alt = pd.read_csv(duel / "heuristic/camions_B.csv").rename(
        columns={"supplier": "supplier_id", "date": "delivery_date", "cost_eur": "cost",
                 "kg": "weight", "m3": "volume"})
    alt_lines = pd.read_csv(duel / "heuristic/affectations_B.csv").rename(
        columns={"supplier": "supplier_id", "date": "delivery_date", "due": "date",
                 "advance_days": "anticipation_days", "dock": "unloading_point",
                 "quantity": "schedule_quantity", "kg": "weight", "m3": "volume", "km": "distance"})
    alt = alt.merge(alt_lines[["truck_id", "plant"]].drop_duplicates(), on="truck_id", validate="1:1")
    payload = identity + ["schedule_quantity", "weight", "volume", "distance"]
    pd.testing.assert_frame_equal(
        base_lines[payload].sort_values(identity).reset_index(drop=True),
        alt_lines[payload].sort_values(identity).reset_index(drop=True), check_dtype=False,
    )
    pairs = base.groupby(KEY).size().index
    if not pairs.equals(alt.groupby(KEY).size().index):
        raise ValueError("The two plans cover different pairs")
    dates = sorted(set(base.delivery_date) | set(alt.delivery_date))

    def calendar(trucks):
        return (trucks.groupby(KEY + ["delivery_date"]).size().unstack(fill_value=0)
                .reindex(index=pairs, columns=dates, fill_value=0).to_numpy())

    left, right = calendar(base), calendar(alt)
    daily_base = left.sum(axis=0)
    differences = (right - left).T
    base_cost = base.groupby(KEY).cost.sum().reindex(pairs).to_numpy()
    alt_cost = alt.groupby(KEY).cost.sum().reindex(pairs).to_numpy()
    delta = alt_cost - base_cost
    n = len(pairs)
    calendar_constraint = LinearConstraint(
        np.column_stack((differences, -np.ones(len(dates)))), -np.inf, -daily_base,
    )
    cost_objective = np.r_[delta, 0.0]
    peak_objective = np.r_[np.zeros(n), 1.0]
    lower = np.zeros(n + 1)
    upper = np.r_[np.ones(n), np.inf]
    rows, choices, calendars = [], [], []

    def record(label, mask, budget=None):
        daily = daily_base + differences @ mask
        alt_pairs = pairs[mask.astype(bool)]
        chosen_lines = pd.concat([
            base_lines.loc[~base_lines.set_index(KEY).index.isin(alt_pairs)],
            alt_lines.loc[alt_lines.set_index(KEY).index.isin(alt_pairs)],
        ])
        assert len(chosen_lines) == len(base_lines) and not chosen_lines.duplicated(identity).any()
        extra = float(delta @ mask)
        if budget is not None and extra > budget + 1e-6:
            raise ValueError("Budget exceeded")
        rows.append(dict(scenario=label, budget_eur=budget, cost_eur=round(base_cost.sum() + extra, 2),
                         extra_eur=round(extra, 2), peak=int(daily.max()), trucks=int(daily.sum()),
                         peak_dates=";".join(day for day, count in zip(dates, daily) if count == daily.max()),
                         advance_days=round(chosen_lines.anticipation_days.mean(), 3),
                         heuristic_pairs=int(mask.sum())))
        choices.extend(dict(scenario=label, supplier_id=supplier, plant=plant,
                            method="heuristique" if selected else "main")
                       for (supplier, plant), selected in zip(pairs, mask))
        calendars.extend(dict(scenario=label, date=day, trucks=int(count))
                         for day, count in zip(dates, daily))
        return int(daily.max())

    record("main", np.zeros(n, dtype=int))
    record("heuristique", np.ones(n, dtype=int))
    for budget in (0, 500, 1000):
        spend = LinearConstraint(cost_objective, -np.inf, budget)
        best_peak = solve(peak_objective, Bounds(lower, upper), [calendar_constraint, spend], n)[-1]
        best_cost = solve(cost_objective, Bounds(lower, np.r_[upper[:-1], best_peak]),
                          [calendar_constraint, spend], n)
        assert record(f"budget_{budget}", best_cost[:-1], budget) == best_peak

    minimum_peak = solve(peak_objective, Bounds(lower, upper), [calendar_constraint], n)[-1]
    best_cost = solve(cost_objective, Bounds(lower, np.r_[upper[:-1], minimum_peak]),
                      [calendar_constraint], n)
    assert record("meilleur_pic_sans_budget", best_cost[:-1]) == minimum_peak

    output = duel / "lissage"
    output.mkdir(parents=True, exist_ok=True)
    summary = pd.DataFrame(rows)
    selected = pd.DataFrame(choices)
    keep_alt = selected.loc[selected.scenario.eq("budget_0") & selected.method.eq("heuristique"), KEY]
    alt_pairs = pd.MultiIndex.from_frame(keep_alt)
    key_truck = KEY + ["delivery_date"]
    def mix(source, other):
        return pd.concat([
            source.loc[~source.set_index(KEY).index.isin(alt_pairs)].assign(method="main"),
            other.loc[other.set_index(KEY).index.isin(alt_pairs)].assign(method="heuristique"),
        ], ignore_index=True)

    def main_ids(frame):
        return frame.assign(truck_id=frame[key_truck + ["truck_no"]].astype(str).agg("|".join, axis=1))

    trucks = mix(main_ids(base), alt)
    lines = mix(main_ids(base_lines), alt_lines)
    truck_key = ["method", "truck_id"]
    loads = lines.groupby(truck_key)[["weight", "volume"]].sum().sort_index()
    manifest = trucks.set_index(truck_key)[["weight", "volume"]].sort_index()
    pd.testing.assert_frame_equal(loads, manifest, atol=1e-8)
    pd.testing.assert_frame_equal(lines[payload].sort_values(identity).reset_index(drop=True),
                                  base_lines[payload].sort_values(identity).reset_index(drop=True), check_dtype=False)
    due = pd.to_datetime(lines.date)
    delivery = pd.to_datetime(lines.delivery_date)
    from delivery_schedule_optimization.profile_optimizer import german_national_holidays
    holidays = german_national_holidays(set(delivery.dt.year))
    expected_groups = trucks[truck_key + key_truck].merge(
        lines[truck_key + key_truck].drop_duplicates(), on=truck_key + key_truck, validate="1:1",
    )
    if (len(expected_groups) != len(trucks) or not (due - delivery).dt.days.between(0, 7).all()
            or delivery.dt.dayofweek.ge(5).any() or delivery.dt.date.isin(holidays).any()
            or trucks.weight.gt(24_500 + 1e-8).any() or trucks.volume.gt(110.4 + 1e-8).any()):
        raise ValueError("Invalid grouping, capacity or delivery date")
    chosen = summary.set_index("scenario").loc["budget_0"]
    if (len(trucks) != chosen.trucks or trucks.groupby("delivery_date").size().max() != chosen.peak
            or abs(trucks.cost.sum() - chosen.cost_eur) > 0.0050001):
        raise ValueError("Export totals differ from selected plan")
    ids = trucks[truck_key].copy()
    ids["camion"] = [f"M-{i:04d}" for i in range(1, len(ids) + 1)]
    trucks = trucks.merge(ids, on=truck_key, validate="1:1")
    lines = lines.merge(ids, on=truck_key, validate="m:1")
    flags = ["weight_imputed", "volume_imputed", "distance_imputed"]
    lines = lines.merge(prepared[identity + flags], on=identity, validate="1:1")
    trucks[["camion", "method", *key_truck, "weight", "volume", "cost"]].to_csv(output / "camions.csv", index=False)
    lines[["camion", "method", *payload, "delivery_date", "anticipation_days", *flags]].to_csv(
        output / "affectations.csv", index=False,
    )
    summary.to_csv(output / "comparatif.csv", index=False)
    selected.to_csv(output / "choix_par_couple.csv", index=False)
    pd.DataFrame(calendars).to_csv(output / "calendrier.csv", index=False)
    seconds = perf_counter() - start
    baseline_cost = published("optimized_kpis").set_index("indicator").loc["coût total (EUR)", "actuel"]
    saving = round(float(baseline_cost) - float(chosen.cost_eur), 2)
    checks = {"under_90_seconds": seconds < 90, "saving_at_least_40000_eur": saving >= 40_000,
              "under_800_trucks": len(trucks) < 800}
    (output / "bilan.json").write_text(json.dumps({
        "commit": commit, "lines": len(lines), "trucks": len(trucks), "peak": int(chosen.peak),
        "cost_eur": float(chosen.cost_eur), "saving_eur": saving, "seconds": round(seconds, 3),
        "checks": checks,
    }, indent=2), encoding="utf-8")
    print(summary.to_string(index=False))
    print(f"{seconds:.2f} s")
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--commit", default="db269fc")
    parser.add_argument("--duel", type=Path, default=Path("results/duel-db269fc"))
    args = parser.parse_args()
    compare(args.repo, args.commit, args.duel)
