"""Combine certified pair bounds and the best verified pair plans across runs."""
import argparse
import json
from pathlib import Path

import pandas as pd

from delivery_schedule_optimization.baseline import BaselineConfig
from delivery_schedule_optimization.profile_optimizer import german_national_holidays


KEY = ["scope", "supplier_id", "plant"]


def run(sources, floor_csv, output, earliest=None):
    results = [
        pd.read_csv(path / "couples.csv").assign(run=path.name)
        for path in sources
    ]
    tables = pd.concat(results, ignore_index=True)
    if tables.groupby(KEY)[["lines", "reference"]].nunique().gt(1).any().any():
        raise ValueError("The input scopes or baselines differ")
    winners = tables.sort_values(["upper", "trucks", "run"]).drop_duplicates(KEY)
    strongest = tables.groupby(KEY, as_index=False)["lower"].max()
    floors = pd.read_csv(floor_csv)
    existing_floors = winners.columns.intersection(floors.columns.difference(KEY))
    winners = (winners.drop(columns=["lower", *existing_floors]).merge(strongest, on=KEY, validate="1:1")
               .merge(floors, on=KEY, validate="1:1"))
    if len(winners) != len(floors):
        raise ValueError("Some couples are missing")
    winners["lower"] = winners[["lower", "fenetres_separees"]].max(axis=1)
    winners["certified"] = winners.lower.eq(winners.upper)
    if (winners.lower > winners.upper).any():
        raise ValueError("Lower bound above feasible cost")

    plans = []
    for path in sources:
        chosen = winners.loc[winners.run.eq(path.name), KEY + ["profile"]]
        if chosen.empty:
            continue
        rows = pd.read_csv(path / "affectations.csv", dtype={"prepared_id": str})
        if "cost_truck_eur" not in rows:
            prices = pd.read_csv(path / "camions.csv").rename(columns={"cost_eur": "cost_truck_eur"})
            truck_keys = KEY + ["date", "truck"]
            rows = rows.merge(prices[truck_keys + ["cost_truck_eur"]], on=truck_keys, validate="m:1")
        plans.append(rows.drop(columns="profile", errors="ignore").merge(chosen, on=KEY, validate="m:1"))
    allocations = pd.concat(plans, ignore_index=True)
    if allocations.duplicated(["scope", "prepared_id"]).any():
        raise ValueError("A source line is duplicated")
    coverage = allocations.groupby(KEY).size()
    if not coverage.eq(winners.set_index(KEY).lines.reindex(coverage.index)).all() or len(coverage) != len(winners):
        raise ValueError("The retained plans lack coverage")
    trucks = allocations.groupby(KEY + ["date", "truck"], as_index=False).agg(
        lines=("prepared_id", "size"), weight=("weight", "sum"), volume=("volume", "sum"),
        cost_eur=("cost_truck_eur", "first"), price_variants=("cost_truck_eur", "nunique"),
    )
    delivery, due = pd.to_datetime(allocations.date), pd.to_datetime(allocations.besoin)
    holidays = german_national_holidays(set(delivery.dt.year))
    if (trucks.weight.gt(24500+1e-8).any() or trucks.volume.gt(110.4+1e-8).any()
            or trucks.price_variants.ne(1).any() or delivery.dt.dayofweek.ge(5).any()
            or delivery.dt.date.isin(holidays).any() or not (due-delivery).dt.days.between(0, 7).all()):
        raise ValueError("Invalid consolidated plan")

    if earliest is not None:
        anchor = due.min()
        days = pd.date_range(anchor, due.max(), freq="B")
        days = days[~pd.Index(days.date).isin(holidays)]
        for _, group in allocations.groupby(KEY):
            dates = pd.to_datetime(group.date)
            needs = pd.to_datetime(group.besoin)
            profile = set(map(int, str(group.profile.iloc[0]).split()))
            regular = dates.dt.dayofweek.isin(profile)
            first = min((day for day in days if day.weekday() in profile), default=None)
            witness = dates.loc[regular].groupby(dates.dt.to_period("W-SUN")).agg(
                lambda week: set(week.dt.dayofweek))
            if (dates.min() < earliest or not any(profile == covered for covered in witness)
                    or (dates.loc[~regular] < anchor).any()
                    or (first is not None and (needs.loc[~regular] >= first).any())):
                raise ValueError("Invalid profile or start-up exception")
        source = pd.read_csv(sources[0] / "affectations.csv", dtype={"prepared_id": str})
        identity = KEY + ["prepared_id"]
        if set(source[identity].itertuples(index=False, name=None)) != set(
                allocations[identity].itertuples(index=False, name=None)):
            raise ValueError("First run must cover the same prepared lines as the consolidated plan")

    output.mkdir(parents=True, exist_ok=True)
    factor = BaselineConfig().price_factor / 100
    report = {}
    for name, group in winners.groupby("scope"):
        a, b, c = (float(group[k].sum()) * factor for k in ("reference", "lower", "upper"))
        valid = trucks.loc[trucks.scope.eq(name)]
        if abs(valid.cost_eur.sum() - c) > .005 or len(valid) != group.trucks.sum():
            raise ValueError("The consolidated amounts differ from the selected plans")
        report[name] = dict(lines=int(group.lines.sum()), groups=len(group), certified=int(group.certified.sum()),
                            trucks=len(valid), reference_eur=round(a, 2), lower_eur=round(b, 2),
                            cost_eur=round(c, 2), saving_eur=round(a-c, 2), gap_eur=round(c-b, 2))
    winners.to_csv(output / "couples.csv", index=False)
    trucks.drop(columns="price_variants").to_csv(output / "camions.csv", index=False)
    allocations.drop(columns="cost_truck_eur").to_csv(output / "affectations.csv", index=False)
    (output / "comparatif.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, action="append", required=True)
    parser.add_argument("--floors", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("results/meilleurs-couts"))
    parser.add_argument("--earliest", type=pd.Timestamp, help="Verify the opened date range and fixed profiles")
    args = parser.parse_args()
    run(args.run, args.floors, args.output, args.earliest)
