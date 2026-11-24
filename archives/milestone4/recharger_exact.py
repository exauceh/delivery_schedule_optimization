"""Minimise tariff over all feasible loads on small, already scheduled days."""
import argparse
from bisect import bisect_left
from functools import lru_cache
import io
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from delivery_schedule_optimization.baseline import BaselineConfig


DAY = ["scope", "supplier_id", "plant", "date"]
PAIR = DAY[:-1]


def optimal_load(weights, volumes, bands, prices):
    n = len(weights)
    loads = []
    slots = [0] * (1 << n)
    total_kg, total_m3 = [0] * (1 << n), [0] * (1 << n)
    for mask in range(1, 1 << n):
        bit = mask & -mask
        i = bit.bit_length() - 1
        total_kg[mask] = weights[i] + total_kg[mask ^ bit]
        total_m3[mask] = volumes[i] + total_m3[mask ^ bit]
        if total_kg[mask] <= 24_500_000 and total_m3[mask] <= 110_400_000:
            slots[mask] = prices[bisect_left(bands, total_kg[mask])]
            loads.append(mask)

    @lru_cache(None)
    def value(remaining):
        if not remaining:
            return 0, 0, ()
        best = (float("inf"), 0, ())
        first = remaining & -remaining
        for load in loads:
            if not load & first or load & remaining != load:
                continue
            cost, trucks, chosen = value(remaining ^ load)
            best = min(best, (cost + slots[load], trucks + 1, (load, *chosen)))
        return best

    _, _, chosen = value((1 << n) - 1)
    return [[i for i in range(n) if mask & (1 << i)] for mask in chosen]


def run(repo, previous, output):
    git = ["git", "-C", str(repo), "-c", f"safe.directory={repo.resolve().as_posix()}",
           "show", "a0e8aba:docs/rate_sheet.csv"]
    rate = pd.read_csv(io.BytesIO(subprocess.check_output(git))).set_index("km_up_to")
    bound = lambda v: np.inf if str(v).startswith(">") else float(v)
    rate.index, rate.columns = rate.index.map(bound), rate.columns.map(bound)
    rate = rate.sort_index().sort_index(axis=1)
    bands = [*[round(b * 1000) for b in rate.columns[:-1]], 24_500_000]
    factor = BaselineConfig().price_factor / 100
    allocation = pd.read_csv(previous / "affectations.csv", dtype={"prepared_id": str})
    prior_trucks = pd.read_csv(previous / "camions.csv")
    original = prior_trucks.groupby(DAY).agg(
        cost=("cost_eur", "sum"), units=("truck", "size"),
    )
    final_lines, final_trucks, changes = [], [], []
    for key, group in allocation.groupby(DAY, sort=True):
        prices = [round(100 * v) for v in rate.iloc[bisect_left(rate.index, group.distance.iloc[0])]]
        old = original.loc[key]
        weights = [round(v * 1000) for v in group.weight]
        volumes = [round(v * 1_000_000) for v in group.volume]
        forced = [[i] for i, (w, v) in enumerate(zip(weights, volumes, strict=True))
                  if w == 24_500_000 or v == 110_400_000]
        rest = [i for i in range(len(group)) if [i] not in forced]
        current = list(group.reset_index(drop=True).groupby("truck").indices.values())
        best = current
        if 0 < len(rest) <= 10:
            subsets = optimal_load([weights[i] for i in rest], [volumes[i] for i in rest], bands, prices)
            best = forced + [[rest[i] for i in subset] for subset in subsets]
        cost = lambda ids: prices[bisect_left(bands, sum(weights[i] for i in ids))] * factor
        total = sum(cost(ids) for ids in best)
        if total > old.cost + 1e-7:
            raise ValueError(f"Exact loading exceeded the previous cost: {key}")
        if total < old.cost - 1e-7 or len(best) != old.units:
            changes.append(dict(zip(DAY, key), old_cost=old.cost, new_cost=total,
                                old_trucks=int(old.units), new_trucks=len(best)))
        for truck, ids in enumerate(best, start=1):
            load = group.iloc[ids].copy()
            load["truck"] = truck
            final_lines.append(load)
            final_trucks.append(dict(zip(DAY, key), truck=truck, lines=len(ids), weight=load.weight.sum(),
                                     volume=load.volume.sum(), cost_eur=cost(ids)))
    lines = pd.concat(final_lines, ignore_index=True)
    trucks = pd.DataFrame(final_trucks)
    if (len(lines) != len(allocation) or lines.duplicated(["scope", "prepared_id"]).any()
            or trucks.weight.gt(24500+1e-8).any() or trucks.volume.gt(110.4+1e-8).any()):
        raise ValueError("Loading lost lines or exceeded capacity")
    evidence = pd.read_csv(previous / "couples.csv").set_index(PAIR)
    totals = trucks.groupby(PAIR).cost_eur.sum()
    evidence["upper"] = np.rint(totals / factor).astype(int)
    evidence["trucks"] = trucks.groupby(PAIR).size()
    evidence["certified"] = evidence.upper.eq(evidence.lower)
    if evidence.upper.lt(evidence.lower).any():
        raise ValueError("Existing lower bound contradicted")
    output.mkdir(parents=True, exist_ok=True)
    lines.to_csv(output / "affectations.csv", index=False)
    trucks.to_csv(output / "camions.csv", index=False)
    evidence.to_csv(output / "couples.csv")
    pd.DataFrame(changes).to_csv(output / "rechargements.csv", index=False)
    report = {}
    for scope, group in evidence.groupby(level="scope"):
        a, b, c = (float(group[k].sum()) * factor for k in ("reference", "lower", "upper"))
        days = trucks.loc[trucks.scope.eq(scope)]
        report[scope] = dict(lines=int(group.lines.sum()), trucks=len(days), cost_eur=round(c, 2),
                             reference_eur=round(a, 2), savings_eur=round(a-c, 2),
                             lower_eur=round(b, 2), gap_eur=round(c-b, 2),
                             certified=int(group.certified.sum()), couples=len(group),
                             peak=int(days.groupby("date").size().max()))
    (output / "bilan.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"{len(changes)} recompositions de camions sur dates inchangées")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--previous", type=Path, default=Path("results/meilleurs-couts"))
    parser.add_argument("--output", type=Path, default=Path("results/meilleurs-couts-affines"))
    args = parser.parse_args()
    run(args.repo, args.previous, args.output)
