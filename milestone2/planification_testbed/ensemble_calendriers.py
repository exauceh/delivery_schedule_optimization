"""Compare a common pool of dated plans, with and without strict RG06."""
import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
import io
from itertools import combinations
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from types import ModuleType

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

from delivery_schedule_optimization.baseline import BaselineConfig
from delivery_schedule_optimization.fast_plan import Line, pack, run as run_heuristic, serialize
from duel_graphe import MAIN, WISSAM


KEY = ["supplier_id", "plant"]
IDENTITY = KEY + ["date", "material", "unloading_point"]


@dataclass
class Candidate:
    source: str
    key: tuple
    weekdays: tuple
    trucks: list
    cents: int
    advance: int
    violations: int
    peak_week_days: int
    rate: object


def select(pool, cost_cap=None, plant_caps=None, peak_first=False):
    if cost_cap is not None:
        minimum_by_pair = {}
        for candidate in pool:
            minimum_by_pair[candidate.key] = min(
                minimum_by_pair.get(candidate.key, candidate.cents), candidate.cents
            )
        slack = cost_cap - sum(minimum_by_pair.values())
        if slack < 0:
            return None
        pool = [p for p in pool if p.cents - minimum_by_pair[p.key] <= slack]
    by_pair = defaultdict(list)
    for i, candidate in enumerate(pool):
        by_pair[candidate.key].append(i)
    days = sorted({truck.day for candidate in pool for truck in candidate.trucks})
    limits = [(None, day) for day in days]
    limits += [(plant, day) for plant in (plant_caps or {}) for day in days]
    index = {key: i + len(by_pair) for i, key in enumerate(limits)}
    n = len(pool)
    matrix = lil_matrix((len(by_pair) + len(limits) + 1, n + 1))
    costs = np.array([p.cents for p in pool] + [0], dtype=float)
    low = np.full(matrix.shape[0], -np.inf)
    high = np.zeros(matrix.shape[0])
    for row, cols in enumerate(by_pair.values()):
        matrix[row, cols] = 1
        low[row] = high[row] = 1
    for j, candidate in enumerate(pool):
        plant = candidate.key[1]
        for day, count in Counter(t.day for t in candidate.trucks).items():
            matrix[index[None, day], j] = count
            if plant_caps:
                matrix[index[plant, day], j] = count
    for (plant, _), row in index.items():
        if plant is None:
            matrix[row, n] = -1
        else:
            high[row] = plant_caps[plant]
    matrix[-1, :] = costs
    high[-1] = cost_cap if cost_cap is not None else np.inf
    constraint = LinearConstraint(matrix.tocsr(), low, high)
    upper = np.r_[np.ones(n), np.inf]

    def solve(objective):
        answer = milp(objective, integrality=np.ones(n + 1), bounds=Bounds(0, upper),
                      constraints=constraint, options={"mip_rel_gap": 0.0, "time_limit": 20})
        if answer.status == 2:
            return None
        if answer.status != 0:
            raise RuntimeError(answer.message)
        return np.rint(answer.x).astype(int)

    if peak_first:
        first = solve(np.r_[np.zeros(n), 1.0])
        if first is None:
            return None
        upper[-1] = first[-1]
    result = solve(costs)
    return None if result is None else [p for p, use in zip(pool, result[:-1], strict=True) if use]


def run(repo, output, heuristic_csv, enrich_peaks=False, profile_pairs=None):
    started = perf_counter()
    output.mkdir(parents=True, exist_ok=True)

    def blob(ref, path):
        return subprocess.check_output(["git", "-C", str(repo), "-c",
                                        f"safe.directory={repo.resolve().as_posix()}",
                                        "show", f"{ref}:{path}"])

    def read(ref, path):
        return pd.read_csv(io.BytesIO(blob(ref, path)))

    graph = ModuleType("ensemble_wissam")
    sys.modules[graph.__name__] = graph
    exec(compile(blob(WISSAM, "src/delivery_schedule_optimization/planner.py"), WISSAM, "exec"), graph.__dict__)
    source = read(MAIN, "data/consolidated_dataset.csv")
    source["date"] = pd.to_datetime(source.date)
    source = source.sort_values(IDENTITY).reset_index(drop=True)
    if source.duplicated(IDENTITY).any():
        raise ValueError("Ambiguous source identifiers")
    raw = read(MAIN, "docs/released_schedule.csv")
    raw["date"] = pd.to_datetime(raw.date)
    provenance = source[IDENTITY].merge(raw[IDENTITY + ["weight", "volume"]], on=IDENTITY,
                                       how="left", validate="1:1", indicator=True)
    if not provenance["_merge"].eq("both").all():
        raise ValueError("Missing source line for the prepared data")
    for measure in ("weight", "volume"):
        source[f"{measure}_imputed"] = provenance[measure].fillna(0).le(0)
    distances = read(MAIN, "docs/distances.csv")
    source["distance_imputed"] = ~source.supplier_id.isin(distances.loc[distances.distance.gt(0), "supplier_id"])
    source["imputed_full_truck"] = source.weight.eq(24_500) | source.volume.eq(110.4)
    start = source.date.min().date() - timedelta(days=7)
    config = graph.PlannerConfig(strict_coverage=True)
    holidays = graph.public_holidays(source, config)
    (output / "rate_sheet.csv").write_bytes(blob(MAIN, "docs/rate_sheet.csv"))
    grid = graph.load_rate_grid(output / "rate_sheet.csv")
    tariff = graph.Tariff(grid, config)
    if heuristic_csv is None:
        (output / "data").mkdir(exist_ok=True)
        (output / "docs").mkdir(exist_ok=True)
        source.drop(columns="imputed_full_truck").to_csv(output / "data/schedule_clean.csv", index=False)
        (output / "docs/rate_sheet.csv").write_bytes(blob(MAIN, "docs/rate_sheet.csv"))
        run_heuristic(output, output / "heuristic", include_imputed=True)
        heuristic_csv = output / "heuristic/affectations_B.csv"
    published = {
        "main": read(MAIN, "data/optimized_lines.csv"),
        "wissam": read(WISSAM, "data/candidat_max_sortie2_affectations.csv").rename(columns={"need_date": "date"}),
        "heuristique": pd.read_csv(heuristic_csv).rename(columns={
            "supplier": "supplier_id", "date": "delivery_date", "due": "date", "dock": "unloading_point",
        }),
    }
    choices = {}
    for name, frame in published.items():
        frame.date = pd.to_datetime(frame.date)
        dates = source[IDENTITY].merge(frame[IDENTITY + ["delivery_date"]], on=IDENTITY, how="outer",
                                       validate="1:1", indicator=True)
        if not dates["_merge"].eq("both").all():
            raise ValueError(f"{name} does not cover the same source lines")
        choices[name] = pd.to_datetime(dates.delivery_date)

    pool = []
    extended = []
    contexts = {}
    all_lines = []
    count = profile_pairs if enrich_peaks and profile_pairs is not None else 180 if enrich_peaks else 40
    top_pairs = set(read(MAIN, "data/optimized_couples.csv").nlargest(count, "optimised_cost")
                    [KEY].itertuples(index=False, name=None))
    for key, group in source.groupby(KEY, sort=True):
        distance = float(group.distance.iloc[0])
        row = grid.iloc[bisect_left(grid.index, distance)]
        bands = tuple(row.index)
        prices = tuple(float(value) * BaselineConfig().price_factor for value in row)
        def rate(kg, bands=bands, prices=prices):
            return prices[bisect_left(bands, kg)]

        lines = [Line(str(i), r.supplier_id, r.plant, r.unloading_point, r.material, r.date.date(),
                      r.schedule_quantity, r.weight, r.volume, r.distance)
                 for i, r in zip(group.index, group.itertuples(index=False), strict=True)]
        all_lines.extend(lines)

        def build(name, dates, lines=lines, key=key, rate=rate):
            dates = [pd.Timestamp(day).date() for day in dates]
            by_day = defaultdict(list)
            for line, day in zip(lines, dates, strict=True):
                if day < start or day.weekday() >= 5 or day in holidays or not 0 <= (line.due - day).days <= 7:
                    raise ValueError(f"Invalid date in {name} for {key}")
                by_day[day].append(line)
            active = sorted(by_day)
            violations = sum(day != max(d for d in active if d <= line.due)
                             for line, day in zip(lines, dates, strict=True))
            trucks = pack(by_day)
            by_week = Counter(day - timedelta(days=day.weekday()) for day in active)
            return Candidate(name, key, tuple(sorted({day.weekday() for day in active})), trucks,
                             round(100 * sum(rate(t.kg) for t in trucks)),
                             sum((line.due - day).days for line, day in zip(lines, dates, strict=True)),
                             violations, max(by_week.values()), rate)

        existing = [build(name, dates.loc[group.index]) for name, dates in choices.items()]
        pool.extend(existing)
        # Price only weekday sets observed in the published candidates. If the strict
        # pool is empty, try all weekdays for that pair as a coverage fallback.
        profiles = {p.weekdays for p in existing}
        strict = []
        for weekdays in sorted(profiles, key=lambda p: (len(p), p)):
            plan = graph.optimal_plan(group, weekdays, holidays, tariff, config, start)
            if plan is not None:
                strict.append(build("graphe", plan.dates))
        if not strict and all(p.violations for p in existing):
            plan = graph.optimal_plan(group, tuple(range(5)), holidays, tariff, config, start, same_days=False)
            if plan is None:
                raise ValueError(f"No strict candidate for {key}")
            strict.append(build("graphe_secours", plan.dates))
        if any(p.violations for p in strict):
            raise ValueError(f"Strict generator violated RG06 for {key}")
        if not any(not p.violations and p.peak_week_days == len(p.weekdays) for p in (*existing, *strict)):
            for day in range(5):
                plan = graph.optimal_plan(group, (day,), holidays, tariff, config, start)
                if plan is not None:
                    candidate = build("graphe_profil_1", plan.dates)
                    if not candidate.violations and candidate.peak_week_days == len(candidate.weekdays):
                        strict.append(candidate)
        pool.extend(strict)
        contexts[key] = group, build
        if key in top_pairs:
            for n in range(1, 6):
                for weekdays in combinations(range(5), n):
                    if weekdays in profiles:
                        continue
                    plan = graph.optimal_plan(group, weekdays, holidays, tariff, config, start)
                    if plan is not None:
                        candidate = build("graphe_elargi", plan.dates)
                        if not candidate.violations and candidate.peak_week_days == len(candidate.weekdays):
                            extended.append(candidate)

    def metrics(choice):
        trucks = [t for p in choice for t in p.trucks]
        peak_by_plant = {
            plant: max(Counter(t.day for p in choice if p.key[1] == plant for t in p.trucks).values())
            for plant in sorted({p.key[1] for p in choice})
        }
        return dict(cost_eur=round(sum(p.rate(t.kg) for p in choice for t in p.trucks), 2),
                    trucks=len(trucks), peak=max(Counter(t.day for t in trucks).values()),
                    advance=round(sum(p.advance for p in choice) / len(source), 3),
                    rg06_violations=sum(p.violations for p in choice),
                    rg05_couples_without_full_week=sum(p.peak_week_days != len(p.weekdays) for p in choice),
                    **peak_by_plant)

    plans = {name: [p for p in pool if p.source == name] for name in published}
    main_caps = {k: v for k, v in metrics(plans["main"]).items() if k.startswith("PLT_")}
    budget = sum(p.cents for p in plans["main"])
    plans["mixte_fenetre"] = select(pool, cost_cap=budget, plant_caps=main_caps, peak_first=True)
    plans["fenetre_budget_main_pic_global"] = select(pool, cost_cap=budget, peak_first=True)
    strict_pool = [p for p in pool if p.violations == 0]
    if len({p.key for p in strict_pool}) != source.groupby(KEY).ngroups:
        raise ValueError("Strict pool does not cover every pair")
    plans["strict_moins_cher"] = select(strict_pool)
    plans["strict_budget_main_pic_global"] = select(strict_pool, cost_cap=budget, peak_first=True)
    strict_same_constraints = select(strict_pool, cost_cap=budget, plant_caps=main_caps, peak_first=True)
    if strict_same_constraints is not None:
        plans["strict_sous_memes_plafonds"] = strict_same_constraints
    strict_caps = {k: v for k, v in metrics(plans["strict_moins_cher"]).items() if k.startswith("PLT_")}
    strict_budget = sum(p.cents for p in plans["strict_moins_cher"])
    plans["strict_sans_surcout"] = select(strict_pool, cost_cap=strict_budget, plant_caps=strict_caps, peak_first=True)
    full_profile_pool = [p for p in strict_pool if p.peak_week_days == len(p.weekdays)] + extended
    if len({p.key for p in full_profile_pool}) != source.groupby(KEY).ngroups:
        raise ValueError("RG05 and RG06 pool does not cover every pair")
    before_day_alternatives = len(full_profile_pool)
    targeted = []
    if enrich_peaks:
        floor_plan = select(full_profile_pool)
        floor = sum(p.cents for p in floor_plan)
        anchor = select(full_profile_pool, cost_cap=max(budget, floor), peak_first=True)
        if anchor is None:
            raise ValueError("No strict anchor plan")
        volume_by_day = Counter(t.day for p in anchor for t in p.trucks)
        targets = sorted(volume_by_day, key=lambda day: (-volume_by_day[day], day))[:3]
        original_nodes = graph.departure_nodes
        for peak_day in targets:
            contributors = sorted(
                (p for p in anchor if any(t.day == peak_day for t in p.trucks)),
                key=lambda p: (-sum(t.day == peak_day for t in p.trucks), -p.cents, p.key),
            )[:10]
            for selected in contributors:
                group, build = contexts[selected.key]
                profiles = [selected.weekdays]
                alternatives = sorted((p for p in full_profile_pool if p.key == selected.key),
                                      key=lambda p: (p.cents, p.weekdays))
                profiles.extend(p.weekdays for p in alternatives if p.weekdays != selected.weekdays)
                for weekdays in list(dict.fromkeys(profiles))[:2]:
                    # Remove this departure date from the graph without declaring it
                    # a holiday: no automatic move to an off-profile weekday.
                    def nodes_without_peak(*args, **kwargs):
                        return [node for node in original_nodes(*args, **kwargs) if node[1] != peak_day]

                    graph.departure_nodes = nodes_without_peak
                    try:
                        plan = graph.optimal_plan(group, weekdays, holidays, tariff, config, start)
                    finally:
                        graph.departure_nodes = original_nodes
                    if plan is None:
                        continue
                    alternative = build("graphe_hors_pointe", plan.dates)
                    if not alternative.violations and alternative.peak_week_days == len(alternative.weekdays):
                        targeted.append(alternative)
        plans["rg05_rg06_avant_ciblage"] = anchor
        # Identical truck arrivals for one pair have the same effect on lissage.
        unique = {}
        for candidate in (*full_profile_pool, *targeted):
            signature = candidate.key, tuple(sorted(Counter(t.day for t in candidate.trucks).items()))
            current = unique.get(signature)
            if current is None or (candidate.cents, candidate.advance) < (current.cents, current.advance):
                unique[signature] = candidate
        full_profile_pool = list(unique.values())
    principal = "principal_rg05_rg06"
    plans[principal] = select(full_profile_pool, cost_cap=budget, peak_first=True)
    if plans[principal] is None:
        lowest_cost = select(full_profile_pool)
        plans["rg05_rg06_moins_cher"] = select(full_profile_pool, cost_cap=sum(p.cents for p in lowest_cost),
                                              peak_first=True)
        del plans[principal]
        principal = "strict_budget_main_pic_global"
    results = pd.DataFrame([dict(scenario=name, **metrics(plan)) for name, plan in plans.items()
                            if plan is not None])
    results.to_csv(output / "comparatif.csv", index=False)
    calendar_rows = []
    for name in ("main", "strict_budget_main_pic_global", "rg05_rg06_moins_cher", "principal_rg05_rg06"):
        if name not in plans or plans[name] is None:
            continue
        counts = Counter(truck.day for candidate in plans[name] for truck in candidate.trucks)
        calendar_rows.extend({"scenario": name, "date": day, "trucks": count}
                             for day, count in sorted(counts.items()))
    pd.DataFrame(calendar_rows).to_csv(output / "calendrier.csv", index=False)
    baseline = read(MAIN, "data/optimized_couples.csv").set_index(KEY).baseline_cost
    for name in dict.fromkeys((principal, "mixte_fenetre", "strict_budget_main_pic_global", "strict_sans_surcout",
                               "rg05_rg06_moins_cher")):
        if name not in plans:
            continue
        choice = plans[name]
        if choice is None:
            continue
        shipments, allocations, stats = serialize([(p.weekdays, p.trucks, p.rate) for p in choice],
                                                  all_lines, holidays, "B")
        report = metrics(choice)
        assert stats["trucks"] == report["trucks"] and stats["cost_eur"] == report["cost_eur"]
        pd.DataFrame(shipments).to_csv(output / f"{name}_camions.csv", index=False)
        flags = ["weight_imputed", "volume_imputed", "distance_imputed"]
        allocation = pd.DataFrame(allocations).merge(
            source[flags].rename_axis("index").reset_index().assign(id=lambda d: d["index"].astype(str)).drop(columns="index"),
            on="id", validate="1:1",
        )
        assert len(allocation) == len(source)
        allocation.to_csv(output / f"{name}_affectations.csv", index=False)
        pd.DataFrame([dict(supplier_id=p.key[0], plant=p.key[1], source=p.source,
                           weekdays=p.weekdays, rg06_violations=p.violations) for p in choice]).to_csv(
            output / f"{name}_choix.csv", index=False)
        print(name, dict(Counter(p.source for p in choice)))
        if name in (principal, "rg05_rg06_moins_cher"):
            prefix = "" if name == principal else "rg05_rg06_"
            weekday_names = ("lundi", "mardi", "mercredi", "jeudi", "vendredi")
            profiles = pd.DataFrame([dict(
                supplier_id=p.key[0], plant=p.key[1], source=p.source,
                profile=len(p.weekdays), weekdays=", ".join(weekday_names[d] for d in p.weekdays),
                peak_week_days=p.peak_week_days, departures=len({t.day for t in p.trucks}),
                trucks=len(p.trucks), baseline_eur=baseline.loc[p.key],
                plan_eur=sum(p.rate(t.kg) for t in p.trucks),
            ) for p in choice])
            profiles["saving_eur"] = profiles.baseline_eur - profiles.plan_eur
            profiles.to_csv(output / f"{prefix}profils_et_economies_par_couple.csv", index=False, float_format="%.2f")
            profiles.groupby("plant")[["trucks", "baseline_eur", "plan_eur", "saving_eur"]].sum().to_csv(
                output / f"{prefix}economies_par_usine.csv", float_format="%.2f",
            )
    manifest = dict(lines=len(source), candidates=len(pool), strict_candidates=len(strict_pool),
                    couples_with_all_profiles_tested=len(top_pairs),
                    rg05_rg06_candidates=len(full_profile_pool),
                    strict_candidates_before_targeting=before_day_alternatives,
                    targeted_candidates=len(targeted),
                    rg05_rg06_under_main_budget="principal_rg05_rg06" in plans,
                    strict_same_constraints_possible=strict_same_constraints is not None,
                    principal=principal,
                    main_plant_caps=main_caps, seconds=round(perf_counter() - started, 3))
    (output / "bilan.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(results.to_string(index=False))
    print(manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("results/ensemble-calendriers"))
    parser.add_argument("--heuristic", type=Path, help="Optional existing heuristic export for the same input")
    parser.add_argument("--enrich-peaks", action="store_true",
                        help="Explore all strict weekday sets and alternatives excluding crowded dates")
    parser.add_argument("--profile-pairs", type=int,
                        help="With --enrich-peaks, limit full profile exploration to this many high-cost pairs")
    args = parser.parse_args()
    if args.profile_pairs is not None and args.profile_pairs < 1:
        parser.error("--profile-pairs must be positive")
    run(args.repo, args.output, args.heuristic, args.enrich_peaks, args.profile_pairs)
