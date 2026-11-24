"""Exact small-instance reference for RG05 and relaxed RG06."""

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import combinations
import warnings

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix


@dataclass(frozen=True)
class Need:
    identity: str
    due: date
    grams: int
    millilitres: int


@dataclass(frozen=True)
class Departure:
    nominal: date | None
    actual: date


@dataclass(frozen=True)
class Truck:
    needs: tuple[int, ...]
    departure: Departure
    cents: int


@dataclass(frozen=True)
class Plan:
    cost: int
    trucks: tuple[Truck, ...]
    weekdays: tuple[int, ...]
    witness: tuple[Departure, ...]


def subsets(values):
    for length in range(1, len(values) + 1):
        yield from combinations(values, length)


def days_between(start, end):
    return (start + timedelta(days=i) for i in range((end - start).days + 1))


def nominal_slots(earliest, last, weekdays, holidays):
    for nominal in days_between(earliest, last + timedelta(days=6)):
        if nominal.weekday() not in weekdays:
            continue
        actual = nominal
        while actual.weekday() >= 5 or actual in holidays:
            actual -= timedelta(days=1)
        if earliest <= actual <= last:
            yield Departure(nominal, actual)


def candidates(needs, profile, earliest, holidays, anchor, max_items):
    regular = tuple(nominal_slots(earliest, max(x.due for x in needs), profile, holidays))
    first_regular = min((slot.actual for slot in regular if anchor and slot.nominal >= anchor), default=None)
    startup = tuple(Departure(None, d) for d in days_between(max(earliest, anchor), first_regular - timedelta(days=1))
                    if d.weekday() < 5 and d not in holidays) if first_regular else ()
    options = defaultdict(list)
    for slot in (*regular, *startup):
        eligible = [i for i, need in enumerate(needs)
                    if 0 <= (need.due - slot.actual).days <= 7
                    and (slot.nominal is not None or need.due < first_regular)]
        if len(eligible) > max_items:
            raise ValueError(f"{len(eligible)} eligible lines on {slot.actual}, limit {max_items}")
        loads = [((), 0, 0)]
        for i in eligible:
            for ids, w, v in loads.copy():
                weight, volume = w + needs[i].grams, v + needs[i].millilitres
                if weight <= 24_500_000 and volume <= 110_400_000:
                    ids = (*ids, i)
                    loads.append((ids, weight, volume))
                    options[ids].append(slot)
    by_week = defaultdict(list)
    for slot in regular:
        monday = slot.nominal - timedelta(days=slot.nominal.weekday())
        by_week[monday].append(slot)
    witnesses = [tuple(slots) for slots in by_week.values() if len(slots) == len(profile)]
    return options, witnesses


def allocate_witness(loads, dates):
    """Return a concrete injective matching from nominal slots to selected loads."""
    def search(todo, assignment):
        if not todo:
            return assignment
        slot, *other = todo
        for k, allowed in enumerate(loads):
            if k not in assignment and slot in allowed:
                found = search(other, {**assignment, k: slot})
                if found is not None:
                    return found
        return None

    return search(list(dates), {})


def solve_profile(needs, profile, earliest, bands, prices, *, holidays=frozenset(), anchor=None,
                  dated=False, max_items=12):
    options, witnesses = candidates(needs, profile, earliest, holidays, anchor, max_items)
    if not witnesses or {i for group in options for i in group} != set(range(len(needs))):
        return None
    columns, costs = [], []
    for group, days in options.items():
        cost = prices[bisect_left(bands, sum(needs[i].grams for i in group))]
        if dated:
            columns.extend((group, (day,)) for day in days)
        else:
            columns.append((group, tuple(days)))
        costs.extend([cost] * (len(days) if dated else 1))
    n = len(columns)
    row, col, values, lower, upper = [], [], [], [], []

    def constraint(terms, low, high):
        for index, coefficient in terms:
            row.append(len(lower))
            col.append(index)
            values.append(coefficient)
        lower.append(low)
        upper.append(high)

    by_need, by_slot = defaultdict(list), defaultdict(set)
    for k, (group, days) in enumerate(columns):
        for i in group:
            by_need[i].append((k, 1))
        for day in days:
            by_slot[day].add(k)
    for i in range(len(needs)):
        constraint(by_need[i], 1, 1)
    for w, dates in enumerate(witnesses):
        for group in ((d,) for d in dates) if dated else subsets(dates):
            eligible = set.union(*(by_slot[d] for d in group))
            constraint([(k, 1) for k in eligible] + [(n + w, -len(group))], 0, np.inf)
    constraint([(n + w, 1) for w in range(len(witnesses))], 1, 1)
    objective = costs + [0] * len(witnesses)
    matrix = coo_matrix((values, (row, col)), shape=(len(lower), len(objective))).tocsc()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Unrecognized options detected.*")
        result = milp(objective, integrality=np.ones(len(objective)), bounds=Bounds(0, 1),
                      constraints=LinearConstraint(matrix, lower, upper),
                      options={"mip_rel_gap": 0, "threads": 1})
    if result.status == 2:
        return None
    if result.status != 0:
        raise RuntimeError(f"Optimality not proved: {result.message}")
    witness = witnesses[next(i for i, v in enumerate(result.x[n:]) if v > .5)]
    chosen = [k for k, v in enumerate(result.x[:n]) if v > .5]
    matching = allocate_witness([columns[k][1] for k in chosen], witness)
    if matching is None:
        raise ValueError("A claimed complete week cannot be scheduled")
    trucks = tuple(Truck(columns[k][0], matching.get(i, columns[k][1][-1]), costs[k])
                   for i, k in enumerate(chosen))
    plan = Plan(sum(t.cents for t in trucks), trucks, profile, witness)
    verify(plan, needs, earliest, holidays, anchor, bands, prices)
    return plan


def verify(plan, needs, earliest, holidays, anchor, bands, prices):
    if sorted(i for t in plan.trucks for i in t.needs) != list(range(len(needs))):
        raise ValueError("Incomplete or duplicate load")
    covered = {t.departure for t in plan.trucks}
    witness_weeks = {d.nominal - timedelta(days=d.nominal.weekday()) for d in plan.witness}
    if (not set(plan.witness) <= covered or len(witness_weeks) != 1
            or {d.nominal.weekday() for d in plan.witness} != set(plan.weekdays)
            or sum(t.cents for t in plan.trucks) != plan.cost):
        raise ValueError("Profile not achieved")
    first = min((s.actual for s in nominal_slots(earliest, max(x.due for x in needs), plan.weekdays, holidays)
                 if anchor and s.nominal >= anchor), default=None)
    for truck in plan.trucks:
        slot = truck.departure
        weight = sum(needs[i].grams for i in truck.needs)
        volume = sum(needs[i].millilitres for i in truck.needs)
        if weight > 24_500_000 or volume > 110_400_000 or truck.cents != prices[bisect_left(bands, weight)]:
            raise ValueError("Invalid load or fare")
        if slot.actual < earliest or slot.actual.weekday() >= 5 or slot.actual in holidays:
            raise ValueError("Invalid effective date")
        if slot.nominal is None:
            if first is None or slot.actual < anchor or any(needs[i].due >= first for i in truck.needs):
                raise ValueError("Invalid start-up exception")
        else:
            actual = slot.nominal
            while actual.weekday() >= 5 or actual in holidays:
                actual -= timedelta(days=1)
            if slot.nominal.weekday() not in plan.weekdays or actual != slot.actual:
                raise ValueError("Invalid nominal date or holiday shift")
        if any(not 0 <= (needs[i].due - slot.actual).days <= 7 for i in truck.needs):
            raise ValueError("Outside the seven-day window")


def solve_all_profiles(needs, earliest, bands, prices, **options):
    best = None
    for profile in subsets(tuple(range(5))):
        plan = solve_profile(needs, profile, earliest, bands, prices, **options)
        if plan is not None and (best is None or (plan.cost, len(plan.trucks)) < (best.cost, len(best.trucks))):
            best = plan
    return best
