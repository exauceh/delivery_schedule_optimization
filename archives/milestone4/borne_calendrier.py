"""Certified cost floors obtained by relaxing the calendar and the loading."""
import argparse
from bisect import bisect_left, bisect_right
from datetime import timedelta
import io
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from delivery_schedule_optimization.baseline import BaselineConfig


def floors(group, bands, prices):
    # Prepared inputs are recorded to at most one gram and one millilitre.
    # Integer units preserve the inclusive tariff thresholds.
    full_kg, full_m3 = 24_500_000, 110_400_000
    bands = [round(value * 1000) for value in bands]
    fixed = 0
    days = {}
    for row in group.itertuples(index=False):
        weight, volume = round(row.weight * 1000), round(row.volume * 1_000_000)
        price = prices[bisect_left(bands, weight)]
        if weight == full_kg or volume == full_m3:
            fixed += price
        else:
            days.setdefault(row.date.date(), []).append((weight, volume, price))
    dates = sorted(days)
    parcels = [parcel for day in dates for parcel in days[day]]
    # DP: minimum cost of integer tariff capacities covering any requested weight.
    unit = 50_000
    demands = (sum(p[0] for p in parcels) + unit - 1) // unit
    cover = [0] * (demands + 1)
    tiers = [(weight // unit, cost) for weight, cost in zip(bands, prices, strict=True)]
    for amount in range(1, demands + 1):
        cover[amount] = min(cost + cover[max(0, amount - weight)] for weight, cost in tiers)

    def pooled(items):
        if not items:
            return 0
        mass, volume, _ = map(sum, zip(*items, strict=True))
        count = max((mass + full_kg - 1) // full_kg, (volume + full_m3 - 1) // full_m3)
        largest = max(p[2] for p in items)
        return max(cover[(mass + unit - 1) // unit], largest + (count - 1) * min(prices),
                   sum(p[2] for p in items if 2 * p[0] > full_kg),
                   sum(p[2] for p in items if 2 * p[1] > full_m3))

    best = [0] * (len(dates) + 1)
    for i in range(len(dates) - 1, -1, -1):
        best[i] = best[i + 1]
        items = []
        for j in range(i, len(dates)):
            items.extend(days[dates[j]])
            next_start = bisect_right(dates, dates[j] + timedelta(days=7))
            best[i] = max(best[i], pooled(items) + best[next_start])
    return fixed, fixed + pooled(parcels), fixed + best[0]


def run(repo, destination, results=None):
    def read(path):
        command = ["git", "-C", str(repo.resolve()), "-c", f"safe.directory={repo.resolve().as_posix()}",
                   "show", f"a0e8aba:{path}"]
        return pd.read_csv(io.BytesIO(subprocess.check_output(command)))

    keys = ["supplier_id", "plant"]
    identity = keys + ["date", "material", "unloading_point"]
    prepared = read("data/consolidated_dataset_max.csv")
    raw = read("docs/released_schedule.csv")
    raw["unknown"] = raw[["weight", "volume"]].fillna(0).le(0).any(axis=1)
    prepared = prepared.merge(raw[identity + ["unknown"]], on=identity, validate="1:1")
    distances = read("docs/distances.csv")
    known = distances.loc[distances.distance.gt(0), "supplier_id"]
    prepared["date"] = pd.to_datetime(prepared.date)
    table = read("docs/rate_sheet.csv").set_index("km_up_to")
    def limit(value):
        return np.inf if str(value).startswith(">") else float(value)
    table.index, table.columns = table.index.map(limit), table.columns.map(limit)
    table = table.sort_index().sort_index(axis=1)
    bands = [*table.columns[:-1], 24500]
    all_rows = []
    for name, sample in (("avec_imputation", prepared),
                         ("sans_imputation_tarifable", prepared.loc[~prepared.unknown & prepared.supplier_id.isin(known)])):
        for key, group in sample.groupby(keys):
            prices = [round(100 * x) for x in table.iloc[bisect_left(table.index, group.distance.iloc[0])]]
            fixed, free, timed = floors(group, bands, prices)
            all_rows.append(dict(scope=name, supplier_id=key[0], plant=key[1], forced=fixed,
                                 sans_calendrier=free, fenetres_separees=timed))
    summary = pd.DataFrame(all_rows)
    if results:
        check = pd.read_csv(results).merge(summary, on=["scope", *keys], validate="1:1")
        if len(check) != len(summary):
            raise ValueError("Different scopes in the two estimates")
        check["combined"] = check[["lower", "fenetres_separees"]].max(axis=1)
        if (check.combined > check.upper).any():
            raise ValueError("Certified floor exceeds feasible cost")
        check["certified_combined"] = check.combined.eq(check.upper)
        destination.mkdir(parents=True, exist_ok=True)
        check.to_csv(destination / "bornes_et_plans.csv", index=False)
    destination.mkdir(parents=True, exist_ok=True)
    summary.to_csv(destination / "bornes.csv", index=False)
    factor = BaselineConfig().price_factor / 100
    print((summary.groupby("scope")[["forced", "sans_calendrier", "fenetres_separees"]].sum() * factor).round(2))
    if results:
        aggregate = check.groupby("scope")[["combined", "upper", "reference", "certified_combined"]].sum()
        report = {}
        for scope, row in aggregate.iterrows():
            report[scope] = dict(lower_eur=round(row.combined*factor, 2), cost_eur=round(row.upper*factor, 2),
                                 gap_eur=round((row.upper-row.combined)*factor, 2),
                                 certified=int(row.certified_combined))
        (destination / "bilan_bornes.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("results/optimisation-cout-v2"))
    parser.add_argument("--couples", type=Path)
    args = parser.parse_args()
    run(args.repo, args.output, args.couples)
