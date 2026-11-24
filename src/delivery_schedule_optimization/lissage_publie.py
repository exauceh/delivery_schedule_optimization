"""Select strictly profiled published plans under plant-specific daily limits."""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from delivery_schedule_optimization.baseline import BaselineConfig
from delivery_schedule_optimization.optimisation_cout import Model
from delivery_schedule_optimization.profile_optimizer import german_national_holidays


KEY = ["supplier_id", "plant"]
LINE_KEY = [*KEY, "besoin", "material", "unloading_point"]
LOAD = ["schedule_quantity", "weight", "volume", "distance"]
FLAGS = ["charge_inconnue", "distance_inconnue"]
SCOPE = "avec_imputation"
WEEKDAYS = "lundi mardi mercredi jeudi vendredi".split()
PRIME = "b492f6cdfb06c9e88ce164f6b54c1b36fe8aa479"


@dataclass(frozen=True)
class Source:
    name: str
    ref: str
    allocations: str
    profiles: str
    date: str = "delivery_date"
    need: str = "need_date"
    truck: str = "truck_id"
    days: str = "profile_days"


SOURCES = (
    Source("prime", PRIME, "milestone4/resultats_verifies/affectations.csv",
           "milestone4/resultats_verifies/couples.csv", "date", "besoin", "truck", "profile"),
    Source("Arianne", "1ee10abcee724e74c4689a6f3124d15498dcb9fb", "sorties/sortie2_camions.csv",
           "sorties/sortie1_profils.csv", "date_livraison", "date_besoin", "camion_id", "jours_autorises"),
    Source("Gilbert 3.1", "8d8f5e4874aa89757e81d5d137aa3a496bf9858e",
           "data/sortie2_affectations.csv", "data/sortie1_profils.csv"),
    Source("presentation", "f69b088706e654332df69fa8039b82b42c0c1910",
           "data/candidat_max_sortie2_affectations.csv", "data/candidat_max_sortie1_profils.csv"),
    Source("main", "13a8ab9ccc9e4a2a1caff98e9a5231222af773c7", "data/optimized_lines.csv",
           "data/optimized_profiles.csv", "delivery_date", "date", "truck_no", "weekdays"),
    Source("Wissam", "4c78cdbc2aebe6d1d79432874141c64ea4553ade",
           "data/candidat_max_sortie2_affectations.csv", "data/candidat_max_sortie1_profils.csv"),
    Source("Gilbert 3", "1542dc7c83c511aeee9081b1d4ce56a45cd113b2",
           "data/sortie2_affectations.csv", "data/sortie1_profils.csv"),
)


@dataclass
class Plan:
    source: str
    profile: tuple[int, ...]
    lines: pd.DataFrame
    trucks: pd.DataFrame

    @property
    def cost(self) -> int:
        return int(self.trucks.cents.sum())


def parse_profile(label: str) -> tuple[int, ...]:
    tokens = label.split()
    if tokens and all(x in "01234" and len(x) == 1 for x in tokens):
        return tuple(sorted(set(map(int, tokens))))
    return tuple(i for i, day in enumerate(WEEKDAYS) if day in label.lower())


def strict_profile(dates: pd.Series, profile: tuple[int, ...]) -> bool:
    if not profile or not dates.dt.weekday.isin(profile).all():
        return False
    required = set(profile)
    return any(set(week.dt.weekday) == required for _, week in dates.groupby(dates.dt.to_period("W-SUN")))


def select_plans(candidates: dict[tuple[str, str], list[Plan]], caps: dict[str, int]) -> dict[tuple[str, str], Plan]:
    model = Model()
    choices, daily, global_daily = {}, defaultdict(list), defaultdict(list)
    counts = {}
    for key, options in candidates.items():
        choices[key] = []
        for plan in options:
            x = model.var(plan.cost)
            choices[key].append((plan, x))
            counts[x] = len(plan.trucks)
            for day, count in plan.trucks.groupby("date").size().items():
                daily[key[1], day].append((x, int(count)))
                global_daily[day].append((x, int(count)))
        model.row([(x, 1) for _, x in choices[key]], low=1, high=1)
    for (plant, _), terms in daily.items():
        model.row(terms, high=caps[plant])

    def solve():
        answer = model.solve(None)
        if answer.status != 0:
            raise ValueError(f"Selection optimality not proved: {answer.message}")
        return answer

    cost = solve()
    amount = round(cost.fun)
    model.row(list(enumerate(model.costs)), low=amount, high=amount)
    model.costs[:] = [0] * len(model.costs)
    peak = model.var(1, upper=sum(caps.values()))
    for terms in global_daily.values():
        model.row([*terms, (peak, -1)], high=0)
    best_peak = round(solve().fun)
    model.row([(peak, 1)], low=best_peak, high=best_peak)
    model.costs[peak] = 0
    for x, count in counts.items():
        model.costs[x] = count
    result = solve()
    return {key: next(plan for plan, x in options if result.x[x] > 0.5) for key, options in choices.items()}


def read_git_csv(repo: Path, ref: str, name: str) -> pd.DataFrame:
    command = ["git", "-C", str(repo), "-c", f"safe.directory={repo.resolve().as_posix()}", "show", f"{ref}:{name}"]
    return pd.read_csv(BytesIO(subprocess.check_output(command)), dtype=str)


def read_plans(repo, source, prepared, price):
    raw = read_git_csv(repo, source.ref, source.allocations).rename(
        columns={source.date: "date", source.need: "besoin", source.truck: "truck"})
    profiles = read_git_csv(repo, source.ref, source.profiles)
    if source.name == "prime":
        raw = raw.loc[raw.scope.eq(SCOPE)]
        profiles = profiles.loc[profiles.scope.eq(SCOPE)]
    patterns = profiles.set_index(KEY)[source.days].map(parse_profile)
    if not patterns.index.is_unique or raw[["date", "truck"]].isna().any().any():
        raise ValueError(f"Missing or duplicate plan keys: {source.name}")

    raw = raw[[*LINE_KEY, *(c for c in LOAD if c in raw), "date", "truck"]]
    matched = raw.merge(prepared, on=LINE_KEY, suffixes=("", "_source"), validate="1:1")
    if len(raw) != len(prepared) or len(matched) != len(prepared):
        raise ValueError(f"Different prepared lines: {source.name}")
    for col in LOAD:
        other = f"{col}_source"
        if other in matched:
            np.testing.assert_allclose(pd.to_numeric(matched[col]), pd.to_numeric(matched[other]), atol=1e-8, rtol=0)
            matched[col] = matched[other]
    date, due = pd.to_datetime(matched.date), pd.to_datetime(matched.besoin)
    holidays = german_national_holidays(set(date.dt.year))
    if (date.lt("2026-11-02").any() or date.dt.weekday.ge(5).any() or date.dt.date.isin(holidays).any()
            or not (due - date).dt.days.between(0, 7).all()):
        raise ValueError(f"Invalid delivery dates: {source.name}")

    plans = {}
    for key, rows in matched.groupby(KEY):
        profile = patterns.loc[key]
        if not strict_profile(pd.to_datetime(rows.date), profile):
            continue
        truck_rows = []
        for (day, truck), items in rows.groupby(["date", "truck"]):
            weight, volume = (sum(map(Decimal, items[col])) for col in ("weight", "volume"))
            if weight > 24500 or volume > Decimal("110.4") or items.distance.nunique() != 1:
                raise ValueError(f"Invalid truck: {source.name} {key} {truck}")
            truck_rows.append(dict(date=day, truck=truck, lines=len(items), weight=float(weight),
                                   volume=float(volume), cents=price(Decimal(items.distance.iloc[0]), weight)))
        plan_lines = rows[["prepared_id", *LINE_KEY, *LOAD, *FLAGS, "date", "truck"]].copy()
        plan_lines["profile"] = " ".join(map(str, profile))
        plans[key] = Plan(source.name, profile, plan_lines, pd.DataFrame(truck_rows))
    return plans


def run(repo: Path, output: Path):
    prepared = read_git_csv(repo, "a0e8aba", "data/consolidated_dataset_max.csv").rename(columns={"date": "besoin"})
    prepared.insert(0, "prepared_id", np.arange(len(prepared)))
    original = read_git_csv(repo, "a0e8aba", "docs/released_schedule.csv").rename(columns={"date": "besoin"})
    original["charge_inconnue"] = original[["weight", "volume"]].apply(pd.to_numeric).fillna(0).le(0).any(axis=1)
    prepared = prepared.merge(original[LINE_KEY + ["charge_inconnue"]], on=LINE_KEY, validate="1:1")
    distances = read_git_csv(repo, "a0e8aba", "docs/distances.csv")
    prepared["distance_inconnue"] = ~prepared.supplier_id.isin(distances.loc[pd.to_numeric(distances.distance).gt(0), "supplier_id"])
    rates = read_git_csv(repo, "a0e8aba", "docs/rate_sheet.csv")
    kilometres = [Decimal(limit) for limit in rates.km_up_to.iloc[:-1]]
    kilograms = [Decimal(limit) for limit in rates.columns[1:-1]]

    def price(distance, weight):
        return int(100 * Decimal(rates.iloc[bisect_left(kilometres, distance), 1 + bisect_left(kilograms, weight)]))

    published = read_git_csv(repo, PRIME, SOURCES[0].profiles)
    baseline = published.loc[published.scope.eq(SCOPE)].set_index(KEY)
    candidates = {key: [] for key in sorted(baseline.index)}
    caps = {}
    for source in SOURCES:
        plans = read_plans(repo, source, prepared, price)
        for key, plan in plans.items():
            candidates[key].append(plan)
        if source.name == "Arianne":
            if set(plans) != set(candidates):
                raise ValueError("The reference for plant capacity lacks strict-profile plans")
            days = pd.concat([plan.trucks.assign(plant=key[1]) for key, plan in plans.items()])
            caps = days.groupby(["plant", "date"]).size().groupby(level="plant").max().astype(int).to_dict()
    selected = select_plans(candidates, caps)
    factor = BaselineConfig().price_factor / 100
    couples, lines, trucks = [], [], []
    for (supplier, plant), plan in selected.items():
        labels = dict(scope=SCOPE, supplier_id=supplier, plant=plant, source=plan.source)
        reference = int(baseline.at[(supplier, plant), "reference"])
        couples.append({**labels, "profile": " ".join(map(str, plan.profile)), "lines": len(plan.lines),
                        "trucks": len(plan.trucks), "cost_eur": plan.cost * factor,
                        "reference_eur": reference * factor, "saving_eur": (reference - plan.cost) * factor})
        lines.append(plan.lines.assign(scope=SCOPE, source=plan.source))
        trucks.append(plan.trucks.assign(**labels, cost_eur=lambda d: d.cents * factor).drop(columns="cents"))
    allocations, loads, selections = pd.concat(lines, ignore_index=True), pd.concat(trucks, ignore_index=True), pd.DataFrame(couples)
    if len(allocations) != len(prepared) or allocations.prepared_id.duplicated().any():
        raise ValueError("Selection does not cover the prepared dataset")
    peaks = loads.groupby(["plant", "date"]).size().groupby(level="plant").max().astype(int).to_dict()
    if any(peak > caps[plant] for plant, peak in peaks.items()):
        raise ValueError("Selection exceeds a plant's daily limit")
    report = dict(scope=SCOPE, lines=len(allocations), couples=len(selections), trucks=len(loads),
                  cost_eur=round(selections.cost_eur.sum(), 2),
                  reference_eur=round(selections.reference_eur.sum(), 2),
                  savings_eur=round(selections.saving_eur.sum(), 2),
                  extra_vs_prime_eur=round(selections.cost_eur.sum() - pd.to_numeric(baseline.upper).sum() * factor, 2),
                  global_peak=int(loads.groupby("date").size().max()), plant_peaks=peaks, plant_limits=caps,
                  eligible_plans=sum(map(len, candidates.values())),
                  source_commits={source.name: source.ref for source in SOURCES})
    output.mkdir(parents=True, exist_ok=True)
    selections.to_csv(output / "couples.csv", index=False)
    allocations.to_csv(output / "affectations.csv", index=False)
    loads.to_csv(output / "camions.csv", index=False)
    (output / "comparatif.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("results/plan-lisse"))
    args = parser.parse_args()
    run(args.repo, args.output)
