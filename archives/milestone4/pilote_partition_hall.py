"""Run the dated/Hall equivalence experiment on the two prepared scopes."""

import argparse
from bisect import bisect_left
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
import io
from pathlib import Path
import subprocess
from time import perf_counter

import pandas as pd

from delivery_schedule_optimization.baseline import BaselineConfig
from delivery_schedule_optimization.partition_hall import Need, days_between, solve_all_profiles
from delivery_schedule_optimization.profile_optimizer import german_national_holidays


PILOT = ("SUP_00092:PLT_002", "SUP_00102:PLT_002", "SUP_00144:PLT_004", "SUP_00001:PLT_004")
KEY = ["supplier_id", "plant"]
LINE = KEY + ["date", "material", "unloading_point"]


def test_group(job):
    scope, key, needs, earliest, anchor, bands, prices, holidays, max_items, imputed = job
    tick = perf_counter()
    a = solve_all_profiles(needs, earliest, bands, prices, anchor=anchor, holidays=holidays, max_items=max_items)
    seconds_hall = perf_counter() - tick
    tick = perf_counter()
    b = solve_all_profiles(needs, earliest, bands, prices, anchor=anchor, holidays=holidays, max_items=max_items,
                           dated=True)
    seconds_dated = perf_counter() - tick
    if a is None or b is None or a.cost != b.cost:
        raise ValueError(f"Exact formulations disagree for {scope}, {key}: {a}, {b}")
    factor = BaselineConfig().price_factor / 100
    supplier, plant = key
    summary = dict(scope=scope, supplier_id=supplier, plant=plant, earliest=earliest, lines=len(needs),
                   lines_imputed=imputed, cost_eur=a.cost * factor, hall_trucks=len(a.trucks),
                   dated_trucks=len(b.trucks), early_trucks=sum(t.departure.actual < anchor for t in a.trucks),
                   hall_seconds=seconds_hall, dated_seconds=seconds_dated, certified=True)
    allocations = []
    for i, truck in enumerate(a.trucks, 1):
        for j in truck.needs:
            allocations.append(dict(scope=scope, supplier_id=supplier, plant=plant, earliest=earliest, truck=i,
                                    line_id=needs[j].identity, due=needs[j].due,
                                    nominal=truck.departure.nominal, actual=truck.departure.actual))
    return summary, allocations


def run(repo, output, couples, all_safe, workers, max_items):
    def read(path):
        command = ["git", "-C", str(repo.resolve()), "-c", f"safe.directory={repo.resolve().as_posix()}",
                   "show", f"a0e8aba:{path}"]
        return pd.read_csv(io.BytesIO(subprocess.check_output(command)))

    data = read("data/consolidated_dataset_max.csv")
    raw = read("docs/released_schedule.csv")
    raw["charge_inconnue"] = raw[["weight", "volume"]].fillna(0).le(0).any(axis=1)
    data = data.merge(raw[LINE + ["charge_inconnue"]], on=LINE, validate="1:1")
    distances = read("docs/distances.csv")
    known = distances.loc[distances.distance.gt(0), "supplier_id"]
    data["imputed"] = data.charge_inconnue | ~data.supplier_id.isin(known)
    data["date"] = pd.to_datetime(data.date)
    anchor = data.date.min().date()
    openings = [anchor - timedelta(days=7)] if all_safe else [anchor, anchor - timedelta(days=7)]
    grid = read("docs/rate_sheet.csv").set_index("km_up_to")
    bound = lambda value: float("inf") if str(value).startswith(">") else float(value)
    grid.index, grid.columns = grid.index.map(bound), grid.columns.map(bound)
    grid = grid.sort_index().sort_index(axis=1)
    bands = tuple([int(1000 * w) for w in grid.columns[:-1]] + [24_500_000])
    holidays = german_national_holidays({anchor.year, data.date.max().year})
    def units(value, scale):
        scaled = Decimal(str(value)) * scale
        if scaled != scaled.to_integral_value() or scaled <= 0:
            raise ValueError(f"Cannot represent prepared load exactly: {value}")
        return int(scaled)

    jobs, skipped = [], []
    for scope, subset in (("avec_imputation", data), ("sans_imputation_tarifable", data.loc[~data.imputed])):
        for key, group in subset.groupby(KEY):
            if not all_safe and ":".join(key) not in couples:
                continue
            needs = [Need(str(i), row.date.date(), units(row.weight, 1000),
                          units(row.volume, 1_000_000))
                     for i, row in zip(group.index, group.itertuples(index=False), strict=True)]
            max_due = max(item.due for item in needs)
            prices = tuple(round(100 * p) for p in grid.iloc[bisect_left(grid.index, group.distance.iloc[0])])
            for earliest in openings:
                largest_day = max(sum(0 <= (line.due - day).days <= 7 for line in needs)
                                  for day in days_between(earliest, max_due))
                if largest_day > max_items:
                    skipped.append(dict(scope=scope, supplier_id=key[0], plant=key[1],
                                        max_eligible_per_day=largest_day, earliest=earliest))
                else:
                    jobs.append((scope, key, needs, earliest, anchor, bands, prices, holidays,
                                 max_items, int(group.imputed.sum())))
    if not jobs:
        raise ValueError("No eligible pilot pairs")
    output.mkdir(parents=True, exist_ok=True)
    summary, allocations = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for report, rows in pool.map(test_group, jobs):
            summary.append(report)
            allocations.extend(rows)
            if len(summary) % 20 == 0:
                print(f"{len(summary)}/{len(jobs)} certified", flush=True)
    table = pd.DataFrame(summary)
    table.to_csv(output / "comparatif.csv", index=False, float_format="%.6f")
    pd.DataFrame(allocations).to_csv(output / "affectations.csv", index=False)
    pd.DataFrame(skipped).to_csv(output / "hors_enumeration.csv", index=False)
    print(table.to_string(index=False) if not all_safe else
          table.groupby("scope").agg(pairs=("lines", "size"), lines=("lines", "sum"),
                                     hall_seconds=("hall_seconds", "sum"),
                                     dated_seconds=("dated_seconds", "sum")).to_string())
    print(f"{len(skipped)} pair/scope combinations outside the enumeration limit")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("results/pilote-hall"))
    parser.add_argument("--couple", action="append")
    parser.add_argument("--all-safe", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-items", type=int, default=10)
    args = parser.parse_args()
    run(args.repo, args.output, args.couple or PILOT, args.all_safe, args.workers, args.max_items)
