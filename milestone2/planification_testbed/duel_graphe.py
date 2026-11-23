"""Compare date assignment on one couple using the same week days, tariff and truck loader."""
import argparse
from collections import defaultdict
from datetime import timedelta
import io
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from types import ModuleType

import numpy as np
import pandas as pd

from delivery_schedule_optimization.baseline import BaselineConfig, shipment_cost
from delivery_schedule_optimization.fast_plan import Line, improve, insert_lines, latest_plan, pack, plan_score


MAIN = "c069c90"
WISSAM = "64e34b9"
PROFILE = (0, 3)
COUPLE = ("SUP_00037", "PLT_002")


def compare(repo: Path, output: Path) -> None:
    def blob(ref, name):
        return subprocess.check_output([
            "git", "-C", str(repo), "-c", f"safe.directory={repo.resolve().as_posix()}",
            "show", f"{ref}:{name}",
        ])

    def read(ref, name):
        return pd.read_csv(io.BytesIO(blob(ref, name)))

    def module(ref, path, name):
        obj = ModuleType(name)
        sys.modules[name] = obj
        exec(compile(blob(ref, path), f"{ref}:{path}", "exec"), obj.__dict__)
        return obj

    main = module(MAIN, "src/delivery_schedule_optimization/engine.py", "duel_main")
    graph = module(WISSAM, "src/delivery_schedule_optimization/planner.py", "duel_wissam")
    source = read(MAIN, "data/consolidated_dataset.csv")
    mask = source.supplier_id.eq(COUPLE[0]) & source.plant.eq(COUPLE[1])
    group = source.loc[mask].copy()
    group["date"] = pd.to_datetime(group.date)
    group["imputed_full_truck"] = group.weight.eq(24_500) | group.volume.eq(110.4)
    start = pd.Timestamp(source.date.min()).date()
    end = group.date.max().date()
    config = graph.PlannerConfig(strict_coverage=True)
    holidays = graph.public_holidays(group, config)
    grid = read(MAIN, "docs/rate_sheet.csv").set_index("km_up_to").astype(float)
    def upper(value):
        return np.inf if str(value).startswith(">") else float(value)
    grid.index = grid.index.map(upper)
    grid.columns = grid.columns.map(upper)
    grid = grid.sort_index().sort_index(axis=1)
    tariff = graph.Tariff(grid, config)
    distance = float(group.distance.iloc[0])
    brackets = grid.columns.to_numpy(float).copy()
    brackets[-1] = 24_500
    prices = grid.iloc[np.searchsorted(grid.index, distance)].to_numpy(float) * config.price_factor
    workdays = [d.date() for d in pd.date_range(start, end)
                if d.weekday() < 5 and d.date() not in holidays]
    lines = [Line(str(i), r.supplier_id, r.plant, r.unloading_point, r.material, r.date.date(),
                  r.schedule_quantity, r.weight, r.volume, r.distance)
             for i, r in zip(group.index, group.itertuples(index=False), strict=True)]
    dates = {line.due: tuple(d for d in reversed(workdays)
                            if d.weekday() in PROFILE and 0 <= (line.due - d).days <= 7)
             for line in lines}
    if not all(dates.values()):
        raise ValueError("The common profile has an uncovered due date")

    def rate(weight):
        return float(shipment_cost(grid, distance, weight, 1, BaselineConfig()))

    assigned = {}
    summary = []

    def record(name, deliveries, own_cost=None):
        deliveries = [pd.Timestamp(d).date() for d in deliveries]
        assert all(d in dates[line.due] for line, d in zip(lines, deliveries, strict=True))
        by_day = defaultdict(list)
        for line, day in zip(lines, deliveries, strict=True):
            by_day[day].append(line)
        active = sorted(by_day)
        weeks = defaultdict(set)
        for day in active:
            weeks[day - timedelta(days=day.weekday())].add(day.weekday())
        assert any(days == set(PROFILE) for days in weeks.values())
        trucks = pack(by_day)
        solver_loads = [main.load_delivery(
            np.array([line.kg for line in items]), np.array([line.m3 for line in items]),
            prices, brackets, settings,
        ) for items in by_day.values()]
        late_block = sum(day != max(d for d in active if d <= line.due)
                         for line, day in zip(lines, deliveries, strict=True))
        summary.append(dict(method=name, common_cost_eur=plan_score(trucks, rate)[0] / 100,
                            solver_loading_cost_eur=round(sum(load.truck_costs.sum() for load in solver_loads), 2),
                            solver_loading_trucks=sum(load.n_trucks for load in solver_loads),
                            native_cost_eur=own_cost, trucks=len(trucks), departures=len(active),
                            days_advance=round(np.mean([(l.due-d).days for l, d in zip(lines, deliveries)]), 3),
                            rg06_violations=late_block))
        assigned[name] = deliveries

    instance = main.CoupleInstance(*COUPLE, distance, group.index.to_numpy(np.int64),
                                   group.date.to_numpy(dtype="datetime64[D]"), group.weight.to_numpy(float),
                                   group.volume.to_numpy(float), prices, brackets)
    settings = main.SolverSettings(24_500, 110.4, 7, 0.01, 30, 0)
    solved = main.solve_couple(instance, len(PROFILE), np.array(workdays, dtype="datetime64[D]"),
                               settings, weekdays=PROFILE)
    record("main_fixe", solved.delivery_days, solved.model_cost)

    def from_trucks(trucks):
        lut = {line.id: truck.day for truck in trucks for line in truck.lines}
        return [lut[line.id] for line in lines]

    latest = latest_plan(lines, dates)
    record("heuristique_dernier_jour", from_trucks(latest))
    alternate = []
    insert_lines(lines, dates, rate, alternate)
    cutoff = perf_counter() + 5
    plans = [improve(plan, dates, rate, cutoff) for plan in (latest, alternate)]
    for best in sorted(plans, key=lambda t: plan_score(t, rate)):
        # Apply the same condition as main: at least one week uses both selected days.
        try:
            record("heuristique_amelioree", from_trucks(best))
        except AssertionError:
            continue
        break

    strict = graph.optimal_plan(group, PROFILE, holidays, tariff, config, start)
    if strict is None:
        raise ValueError("No strict graph plan")
    record("graphe_strict", strict.dates, strict.cost)
    relaxed = graph.reassign(group, strict, holidays, tariff, graph.PlannerConfig(), start)
    record("graphe_puis_reaffectation", relaxed.dates, relaxed.cost)

    output.mkdir(parents=True, exist_ok=True)
    report = pd.DataFrame(summary)
    report.to_csv(output / "comparatif.csv", index=False)
    group[["date", "material", "unloading_point", "weight", "volume"]].assign(**assigned).to_csv(
        output / "dates_par_ligne.csv", index=False,
    )
    print(f"{len(lines)} lines | {start} to {end} | main solver status: {solved.status}")
    print(report.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("results/duel-wissam"))
    args = parser.parse_args()
    compare(args.repo, args.output)
