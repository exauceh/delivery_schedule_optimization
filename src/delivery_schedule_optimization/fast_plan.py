"""Produce a timed, auditable plan on the measured and priced testbed scope."""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
import json
from pathlib import Path
from time import perf_counter

import pandas as pd

from .baseline import BaselineConfig, load_prepared_lines, load_rate_grid
from .profile_optimizer import candidate_calendars, german_national_holidays


CONFIG = BaselineConfig()
MAX_ADVANCE = 7


@dataclass(frozen=True)
class Line:
    id: str
    supplier: str
    plant: str
    dock: str
    material: str
    due: date
    quantity: float
    kg: float
    m3: float
    km: float


@dataclass
class Truck:
    day: date
    lines: list[Line] = field(default_factory=list)
    kg: float = 0.0
    m3: float = 0.0

    def fits(self, line: Line) -> bool:
        return self.kg + line.kg <= CONFIG.truck_kg and self.m3 + line.m3 <= CONFIG.truck_m3 + 1e-9

    def add(self, line: Line) -> None:
        self.lines.append(line)
        self.kg += line.kg
        self.m3 += line.m3


def load_order(line: Line) -> tuple:
    return (-max(line.kg / CONFIG.truck_kg, line.m3 / CONFIG.truck_m3), line.id)


def pack(assignments: dict[date, list[Line]]) -> list[Truck]:
    result = []
    for day, lines in sorted(assignments.items()):
        trucks: list[Truck] = []
        for line in sorted(lines, key=load_order):
            truck = next((truck for truck in trucks if truck.fits(line)), None)
            if truck is None:
                truck = Truck(day)
                if not truck.fits(line):
                    raise ValueError(f"Line exceeds capacity: {line.id}")
                trucks.append(truck)
            truck.add(line)
        result.extend(trucks)
    return result


def plan_score(trucks: list[Truck], rate) -> tuple:
    return (
        round(sum(rate(truck.kg) for truck in trucks) * 100),
        len(trucks),
        sum((line.due - truck.day).days for truck in trucks for line in truck.lines),
    )


def dates_for(lines: list[Line], profile: tuple[int, ...], holidays: set[date]) -> dict[date, tuple[date, ...]]:
    options = {}
    for due in sorted({line.due for line in lines}):
        days = (due - timedelta(days=advance) for advance in range(MAX_ADVANCE + 1))
        options[due] = tuple(day for day in days if day.weekday() in profile and day not in holidays)
    return options


def latest_plan(lines: list[Line], options: dict[date, tuple[date, ...]]) -> list[Truck]:
    assignments = defaultdict(list)
    for line in lines:
        assignments[options[line.due][0]].append(line)
    return pack(assignments)


def insert_lines(lines: list[Line], options: dict[date, tuple[date, ...]], rate, trucks: list[Truck]) -> None:
    """Insert the hardest lines first, comparing each allowed date and marginal tariff."""
    by_day = defaultdict(list)
    for truck in trucks:
        by_day[truck.day].append(truck)
    for line in sorted(lines, key=lambda line: (len(options[line.due]), line.due, load_order(line))):
        choices = []
        for day in options[line.due]:
            choices.append(((round(rate(line.kg), 8), 1, -day.toordinal(), 0), None, day))
            for index, truck in enumerate(by_day[day]):
                if truck.fits(line):
                    extra_cost = rate(truck.kg + line.kg) - rate(truck.kg)
                    choices.append(((round(extra_cost, 8), 0, -day.toordinal(), index), truck, day))
        _, truck, day = min(choices, key=lambda choice: choice[0])
        if truck is None:
            truck = Truck(day)
            trucks.append(truck)
            by_day[day].append(truck)
        truck.add(line)


def improve(trucks: list[Truck], options: dict[date, tuple[date, ...]], rate, cutoff: float) -> list[Truck]:
    """Try emptying up to three trucks per pass, with two passes at most."""
    for _ in range(2):
        previous = trucks
        targets = sorted(trucks, key=lambda t: (max(t.kg / CONFIG.truck_kg, t.m3 / CONFIG.truck_m3), t.day))[:3]
        for target in targets:
            if perf_counter() >= cutoff:
                return trucks
            candidate = [Truck(t.day, t.lines.copy(), t.kg, t.m3) for t in previous if t is not target]
            insert_lines(target.lines, options, rate, candidate)
            if plan_score(candidate, rate) < plan_score(trucks, rate):
                trucks = candidate
        if trucks is previous:
            break
    return trucks


def choose_profile(lines: list[Line], holidays: set[date], rate, cutoff: float) -> tuple[tuple[int, ...], list[Truck]]:
    profiles = candidate_calendars()
    best = None
    # Complete a feasible seed before starting the optional search.
    for profile in profiles:
        if best is not None and len(profile) > 1:
            break
        options = dates_for(lines, profile, holidays)
        if any(not days for days in options.values()):
            continue
        trucks = latest_plan(lines, options)
        score = (*plan_score(trucks, rate), len(profile), profile)
        if best is None or score < best[0]:
            best = (score, profile, options, trucks)
    if best is None:
        raise ValueError(f"No feasible profile for {lines[0].supplier}")

    # The next phase evaluates all calendars while time permits.
    for profile in profiles:
        if perf_counter() >= cutoff:
            break
        options = dates_for(lines, profile, holidays)
        if any(not days for days in options.values()):
            continue
        alternate: list[Truck] = []
        insert_lines(lines, options, rate, alternate)
        for trucks in (latest_plan(lines, options), alternate):
            score = (*plan_score(trucks, rate), len(profile), profile)
            if score < best[0]:
                best = (score, profile, options, trucks)
    _, profile, options, trucks = best
    return profile, improve(trucks, options, rate, cutoff)


def serialize(plans, source: list[Line], holidays: set[date], scenario: str) -> tuple[list[dict], list[dict], dict]:
    seen = set()
    arrivals = defaultdict(int)
    shipment_rows, allocations = [], []
    for profile, trucks, rate in plans:
        for truck in trucks:
            truck_id = f"{scenario}-{len(shipment_rows) + 1:05d}"
            if truck.day.weekday() not in profile or truck.day in holidays or not truck.lines:
                raise ValueError(f"Invalid departure: {truck_id}")
            if truck.kg > CONFIG.truck_kg + 1e-9 or truck.m3 > CONFIG.truck_m3 + 1e-9:
                raise ValueError(f"Capacity exceeded: {truck_id}")
            if round(sum(line.kg for line in truck.lines) - truck.kg, 9) or round(sum(line.m3 for line in truck.lines) - truck.m3, 9):
                raise ValueError(f"Invalid load totals: {truck_id}")
            if len({line.supplier for line in truck.lines}) != 1 or (
                scenario in ("A", "B") and len({line.plant for line in truck.lines}) != 1
            ):
                raise ValueError(f"Mixed grouping: {truck_id}")
            shipment_rows.append(dict(truck_id=truck_id, date=truck.day, supplier=truck.lines[0].supplier,
                                      profile=" ".join(str(day + 1) for day in profile),
                                      kg=truck.kg, m3=truck.m3, cost_eur=rate(truck.kg)))
            arrivals[truck.day] += 1
            for line in truck.lines:
                advance = (line.due - truck.day).days
                if line.id in seen or not 0 <= advance <= MAX_ADVANCE:
                    raise ValueError(f"Duplicate or invalid date for {line.id}")
                seen.add(line.id)
                allocations.append(dict(truck_id=truck_id, date=truck.day, advance_days=advance, **vars(line)))
    if seen != {line.id for line in source}:
        raise ValueError(f"Incomplete coverage in {scenario}")
    stats = dict(scenario=scenario, lines=len(source), trucks=len(shipment_rows),
                 cost_eur=round(sum(row["cost_eur"] for row in shipment_rows), 2),
                 peak_trucks_per_day=max(arrivals.values()),
                 max_advance_days=max(row["advance_days"] for row in allocations))
    return shipment_rows, allocations, stats


def run(root: Path, output: Path, *, include_imputed: bool = False) -> dict:
    start = perf_counter()
    cutoff = start + 70
    prepared = load_prepared_lines(root / "data" / "schedule_clean.csv")
    grid = load_rate_grid(root / "docs" / "rate_sheet.csv")
    # Use the same measured and priced scope as the previous testbed comparison.
    eligible = prepared if include_imputed else prepared.loc[~prepared["any_imputed"]]
    lines = [Line(r.prepared_line_id, r.supplier_id, r.plant, r.unloading_point, r.material,
                  r.date.date(), r.schedule_quantity, r.weight, r.volume, r.distance)
             for r in eligible.itertuples(index=False)]
    if not lines:
        raise ValueError("No eligible lines")
    holidays = german_national_holidays({line.due.year for line in lines} | {
        (line.due - timedelta(days=MAX_ADVANCE)).year for line in lines
    })
    def price(distance: float):
        row = grid.iloc[bisect_left(grid.index, distance)]
        bands = tuple(row.index)
        prices = tuple(float(value) * CONFIG.price_factor for value in row)
        return lambda kg: prices[bisect_left(bands, kg)]

    pairs, suppliers = defaultdict(list), defaultdict(list)
    for line in lines:
        if not Truck(line.due).fits(line):
            raise ValueError(f"Line exceeds truck capacity: {line.id}")
        pairs[(line.supplier, line.plant)].append(line)
        suppliers[line.supplier].append(line)
    if any(len({line.km for line in group}) != 1 for group in suppliers.values()):
        raise ValueError("Inconsistent supplier distances")
    rates = {supplier: price(group[0].km) for supplier, group in suppliers.items()}
    weekdays = tuple(range(5))
    baseline = [
        (weekdays, latest_plan(group, dates_for(group, weekdays, holidays)), rates[group[0].supplier])
        for group in pairs.values()
    ]
    # Build a complete initial plan for B and C before spending time on improvements.
    plans = {"A": baseline, "B": [], "C": []}
    for scenario, groups in (("C", suppliers), ("B", pairs)):
        for group in sorted(groups.values(), key=lambda group: (-len(group), group[0].id)):
            rate = rates[group[0].supplier]
            profile, trucks = choose_profile(group, holidays, rate, cutoff=0)
            plans[scenario].append((profile, trucks, rate))
    initial = {
        scenario: {
            "trucks": sum(len(trucks) for _, trucks, _ in plans[scenario]),
            "cost_eur": round(sum(rate(truck.kg) for _, trucks, rate in plans[scenario] for truck in trucks), 2),
        }
        for scenario in ("B", "C")
    }
    for scenario, groups in (("C", suppliers), ("B", pairs)):
        for index, group in enumerate(sorted(groups.values(), key=lambda group: (-len(group), group[0].id))):
            if perf_counter() >= cutoff:
                break
            rate = rates[group[0].supplier]
            profile, trucks = choose_profile(group, holidays, rate, cutoff)
            existing_profile, existing_trucks, _ = plans[scenario][index]
            if (*plan_score(trucks, rate), len(profile), profile) < (
                *plan_score(existing_trucks, rate), len(existing_profile), existing_profile
            ):
                plans[scenario][index] = profile, trucks, rate
    output.mkdir(parents=True, exist_ok=True)
    summaries = []
    for scenario, plan in plans.items():
        shipments, allocations, stats = serialize(plan, lines, holidays, scenario)
        pd.DataFrame(shipments).to_csv(output / f"camions_{scenario}.csv", index=False)
        pd.DataFrame(allocations).to_csv(output / f"affectations_{scenario}.csv", index=False)
        summaries.append(stats)
    prepared.loc[~prepared.index.isin(eligible.index), [
        "prepared_line_id", "weight_imputed", "volume_imputed", "distance_imputed"
    ]].to_csv(output / "exclusions_preparees.csv", index=False)
    for stats in summaries:
        stats["saving_eur"] = round(summaries[0]["cost_eur"] - stats["cost_eur"], 2)
    pd.DataFrame(summaries).to_csv(output / "comparatif.csv", index=False)
    elapsed = perf_counter() - start
    hub = summaries[-1]
    result = dict(seconds=round(elapsed, 3), initial_plans=initial, scenarios=summaries, checks={
        "under_90_seconds": elapsed < 90,
        "at_least_40000_eur": hub["saving_eur"] >= 40_000,
        "under_800_trucks": hub["trucks"] < 800,
    })
    (output / "bilan.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("results/fast_plan"))
    arguments = parser.parse_args()
    report = run(arguments.root, arguments.output)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if all(report["checks"].values()) else 1)
