"""Cost-first audit: feasible truck plans and certified lower bounds, by pair."""
import argparse
from bisect import bisect_left
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from decimal import Decimal
import io
from itertools import combinations
import json
from pathlib import Path
import subprocess
import warnings

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from delivery_schedule_optimization.baseline import BaselineConfig
from delivery_schedule_optimization.fast_plan import Line, pack
from delivery_schedule_optimization.profile_optimizer import german_national_holidays

REF = "a0e8aba"
CAP_KG, CAP_M3 = 24500, 110.4
KEY = ["supplier_id", "plant"]
ROW = KEY + ["date", "material", "unloading_point"]


class Model:
    def __init__(self):
        self.costs, self.upper = [], []
        self.r, self.c, self.v, self.low, self.high = [], [], [], [], []
        self.continuous = set()

    def var(self, cost=0, upper=1):
        self.costs.append(cost)
        self.upper.append(upper)
        return len(self.costs) - 1

    def row(self, terms, low=-np.inf, high=np.inf):
        for col, value in terms:
            self.r.append(len(self.low))
            self.c.append(col)
            self.v.append(value)
        self.low.append(low)
        self.high.append(high)

    def solve(self, seconds):
        matrix = coo_matrix((self.v, (self.r, self.c)), shape=(len(self.low), len(self.costs))).tocsc()
        options = {"mip_rel_gap": 0, "threads": 1}
        if seconds:
            options["time_limit"] = seconds
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Unrecognized options detected.*")
            integrality = np.ones(len(self.costs))
            if self.continuous:
                integrality[list(self.continuous)] = 0
            return milp(self.costs, integrality=integrality, bounds=Bounds(0, self.upper),
                        constraints=LinearConstraint(matrix, self.low, self.high), options=options)


def scaled_load(value, unit):
    scaled = Decimal(str(value)) * unit
    if scaled <= 0 or scaled != scaled.to_integral_value():
        raise ValueError(f"Invalid load precision for compact model: {value}")
    return int(scaled)


def tariff_patterns(max_mass, max_trucks, bands, prices):
    """Keep sorted lists of truck tiers that could beat a pooled truck."""
    def extend(prefix, capacity, cost, top):
        for tier in range(top, -1, -1):
            upper = capacity + bands[tier]
            fee = cost + prices[tier]
            # All the day's loads fit in one truck. A dominated prefix can
            # always merge into one truck, even when the pattern is extended.
            if prefix and prices[bisect_left(bands, min(max_mass, upper))] <= fee:
                continue
            pattern = (*prefix, tier)
            yield pattern, fee
            if len(pattern) < max_trucks:
                yield from extend(pattern, upper, fee, tier)
    return list(extend((), 0, 0, bisect_left(bands, max_mass)))


def compact_day(model, eligible, weights, bands, prices, used):
    patterns = tariff_patterns(sum(weights[i] for i, _ in eligible), len(eligible), bands, prices)
    selections = [(pattern, model.var(fee)) for pattern, fee in patterns]
    model.row([(x, 1) for _, x in selections] + [(used, -1)], low=0, high=0)
    coverage = defaultdict(list)
    extra_slots = []
    for k in range(1, max(len(pattern) for pattern, _ in selections)):
        possible = [(pattern[k], x) for pattern, x in selections if len(pattern) > k]
        largest = max(bands[tier] for tier, _ in possible)
        pieces = [(i, model.var()) for i, _ in eligible if weights[i] <= largest]
        for i, x in pieces:
            coverage[i].append(x)
            model.row([(x, 1)] + [(v, -1) for tier, v in possible if bands[tier] >= weights[i]], high=0)
        load = [(x, weights[i]) for i, x in pieces]
        model.row(load + [(v, -bands[tier]) for tier, v in possible], high=0)
        model.row(load + [(v, -(bands[tier - 1] + 1 if tier else 1)) for tier, v in possible], low=0)
        extra_slots.append(pieces)
    primary = []
    for i, x in eligible:
        model.row([(x, -1)] + [(v, 1) for v in coverage[i]], high=0)
        primary.append((x, weights[i]))
        primary.extend((v, -weights[i]) for v in coverage[i])
    model.row(primary + [(v, -bands[p[0]]) for p, v in selections], high=0)
    model.row(primary + [(v, -(bands[p[0] - 1] + 1 if p[0] else 1)) for p, v in selections], low=0)
    return extra_slots


def add_window_cost_cuts(model, dates, by_line, lines, bands, prices, tight=False):
    """Every demand restricted to a date interval must be paid for inside it."""
    day_of_line = [(options[0][0], options[-1][0]) for options in by_line]
    grams = [scaled_load(line.kg, 1000) for line in lines]
    volume = [scaled_load(line.m3, 1_000_000) for line in lines]
    band_grams = [round(cap * 1000) for cap in bands]
    if any(cap % 50_000 for cap in band_grams):
        raise ValueError("Window tariff bound requires tiers in multiples of 50 kg")
    unit_caps = [cap // 50_000 for cap in band_grams]
    cover = [0]
    for weight in range(1, (sum(grams) + 49_999) // 50_000 + 1):
        cover.append(min(price + cover[max(0, weight - cap)] for cap, price in zip(unit_caps, prices)))
    windows = defaultdict(list)
    minimum_price = min(prices)
    for first in range(len(dates)):
        for length in sorted({5, 8, 11, 16, len(dates)}):
            last = first + length - 1
            if last >= len(dates):
                continue
            inside = [i for i, (a, b) in enumerate(day_of_line) if first <= a <= b <= last]
            if not inside:
                continue
            full = [i for i in inside if grams[i] == 24_500_000 or volume[i] == 110_400_000]
            remaining = set(inside) - set(full)
            demand = (sum(grams[i] for i in remaining) + 49_999) // 50_000
            count = (sum(volume[i] for i in remaining) + 110_399_999) // 110_400_000
            biggest = max((prices[bisect_left(band_grams, grams[i])] for i in remaining), default=0)
            floor = sum(prices[bisect_left(band_grams, grams[i])] for i in full)
            pooled = cover[demand]
            if tight and remaining:
                # The latest allowable date of the earliest-ending interval is
                # an optimal first point for covering interval demand.
                min_days, last_point = 0, -1
                for start, end in sorted((day_of_line[i] for i in remaining), key=lambda interval: interval[1]):
                    if start > last_point:
                        min_days += 1
                        last_point = end
                count = max(count, min_days)
                mass_per_day = [0] * length
                for i in remaining:
                    for day, _ in by_line[i]:
                        mass_per_day[day - first] += grams[i]
                cap = (max(mass_per_day) + 49_999) // 50_000
                tiers = tuple(zip((min(cap, tier) for tier in unit_caps), prices))
                if cap < unit_caps[-1] or count > 1:
                    costs = [0]
                    for weight in range(1, demand + 1):
                        costs.append(min(price + costs[max(0, weight - size)] for size, price in tiers))
                    for _ in range(count):
                        costs = [min(price + costs[max(0, weight - size)] for size, price in tiers)
                                 for weight in range(demand + 1)]
                    pooled = costs[demand]
            floor += max(pooled, biggest, count * minimum_price)
            terms = [(v, model.costs[v]) for cols in dates[first:last + 1] for v in cols if model.costs[v]]
            model.row(terms, low=floor)
            windows[first].append((last, floor))
    disjoint = [0] * (len(dates) + 1)
    for start in range(len(dates) - 1, -1, -1):
        disjoint[start] = max([disjoint[start + 1], *(value + disjoint[end + 1] for end, value in windows[start])])
    return disjoint[0]


def solve_pair(job):
    scope, lines, workdays, bands, prices, published, exact_size, bound_seconds, joint, anchor, *options = job
    compact_joint = bool(options and options[0])
    adaptive = len(options) > 1 and options[1]
    window_cuts = len(options) > 2 and options[2]
    strict_cutoff = len(options) > 3 and options[3]
    prior_lower = options[4] if len(options) > 4 else None
    branch_profile = options[5] if len(options) > 5 else None
    branch_upper = options[6] if len(options) > 6 else None
    fractional = len(options) > 7 and options[7]
    tight_windows = len(options) > 8 and options[8]
    full_items = set()
    if compact_joint:
        joint = True
        gram_bands = tuple(round(bound * 1000) for bound in bands)
        grams = tuple(scaled_load(line.kg, 1000) for line in lines)
        millilitres = tuple(scaled_load(line.m3, 1_000_000) for line in lines)
        full_items = {i for i, (mass, volume) in enumerate(zip(grams, millilitres))
                      if mass == 24_500_000 or volume == 110_400_000}
        if any(p <= 0 for p in prices) or list(prices) != sorted(prices):
            raise ValueError("Compact tariff patterns require positive, nondecreasing fares")
    positions = {line.id: i for i, line in enumerate(lines)}
    model = Model()
    weekdays = [model.var() for _ in range(5)]
    used_days = {}
    eligible_days = defaultdict(list)
    by_line = [[] for _ in lines]
    first = [next((d for d in workdays if d >= anchor and d.weekday() == w), None) for w in range(5)]
    first_branch = min((first[w] for w in branch_profile if first[w] is not None), default=None) if branch_profile else None
    for j, day in enumerate(workdays):
        for i, line in enumerate(lines):
            allowed = (branch_profile is None or day.weekday() in branch_profile
                       or (day >= anchor and (first_branch is None or line.due < first_branch)))
            if allowed and 0 <= (line.due - day).days <= 7:
                x = model.var()
                by_line[i].append((j, x))
                eligible_days[j].append((i, x))
    if branch_profile is not None:
        if not all(by_line):
            return dict(lower=branch_upper, upper=None), []
        for w, x in enumerate(weekdays):
            model.row([(x, 1)], low=int(w in branch_profile), high=int(w in branch_profile))
    for choices in by_line:
        model.row([(x, 1) for _, x in choices], low=1, high=1)
    if adaptive:
        # Keep the physical model where its costly fallback stays small.
        flexible = [[i for i, _ in eligible if i not in full_items] for eligible in eligible_days.values()]
        dense = any(len(ids) > 16 and (sum(grams[i] for i in ids) > 24_500_000
                                       or sum(millilitres[i] for i in ids) > 110_400_000)
                    for ids in flexible)
        if dense:
            compact_joint = joint = False
    enumerate_loads = max(map(len, eligible_days.values())) <= exact_size
    exact = enumerate_loads or joint
    truck_columns = []
    truck_slots = defaultdict(list)
    compact_primary = {}
    direct_trucks = []
    day_costs = [[] for _ in workdays]
    for j, eligible in eligible_days.items():
        start_columns = len(model.costs)
        day = workdays[j]
        y = used_days[j] = model.var()
        for i, x in eligible:
            model.row([(x, 1), (y, -1)], high=0)
            if day < anchor:
                model.row([(x, 1), (weekdays[day.weekday()], -1)], high=0)
                continue
            for w, first_day in enumerate(first):
                if first_day is not None and first_day <= lines[i].due and w != day.weekday():
                    model.row([(x, 1), (weekdays[day.weekday()], -1), (weekdays[w], 1)], high=1)
        model.row([(y, 1)] + [(x, -1) for _, x in eligible], high=0)
        direct = []
        if compact_joint and not enumerate_loads and full_items:
            direct = [(i, x) for i, x in eligible if i in full_items]
            if direct:
                eligible = [(i, x) for i, x in eligible if i not in full_items]
                for i, x in direct:
                    model.costs[x] = prices[bisect_left(gram_bands, grams[i])]
                    direct_trucks.append((j, i, x))
                if not eligible:
                    day_costs[j] = [x for _, x in direct]
                    continue
                y = model.var()
                for _, x in eligible:
                    model.row([(x, 1), (y, -1)], high=0)
                model.row([(y, 1)] + [(x, -1) for _, x in eligible], high=0)
        if enumerate_loads:
            cover = defaultdict(list)
            subsets = [((), 0.0, 0.0)]
            for i, _ in eligible:
                for members, kg, m3 in subsets.copy():
                    weight, volume = kg + lines[i].kg, m3 + lines[i].m3
                    if weight <= CAP_KG and volume <= CAP_M3 + 1e-9:
                        group = (*members, i)
                        subsets.append((group, weight, volume))
                        truck = model.var(prices[bisect_left(bands, weight)])
                        truck_columns.append((j, group, truck))
                        for person in group:
                            cover[person].append(truck)
            for i, x in eligible:
                model.row([(x, -1)] + [(b, 1) for b in cover[i]], low=0, high=0)
        elif compact_joint and sum(grams[i] for i, _ in eligible) <= 24_500_000 and sum(
                millilitres[i] for i, _ in eligible) <= 110_400_000:
            truck_slots[j] = compact_day(model, eligible, grams, gram_bands, prices, y)
            compact_primary[j] = eligible
        elif joint:
            coverage = defaultdict(list)
            previous = None
            for slot in range(len(eligible)):
                opened = model.var()
                tiers = [model.var(price) for price in prices]
                pieces = [(i, model.var()) for i, _ in eligible]
                for i, x in pieces:
                    coverage[i].append(x)
                    model.row([(x, 1), (opened, -1)], high=0)
                model.row([(v, 1) for v in tiers] + [(opened, -1)], low=0, high=0)
                model.row([(x, lines[i].kg) for i, x in pieces] +
                          [(tier, -bound) for tier, bound in zip(tiers, bands, strict=True)], high=0)
                model.row([(x, lines[i].m3) for i, x in pieces] + [(opened, -CAP_M3)], high=0)
                if previous is not None:
                    model.row([(opened, 1), (previous, -1)], high=0)
                previous = opened
                truck_slots[j].append(pieces)
            for i, x in eligible:
                model.row([(x, -1)] + [(v, 1) for v in coverage[i]], low=0, high=0)
        else:
            # No truck can carry more than all the lines eligible on its day.
            max_kg = sum(lines[i].kg for i, _ in eligible)
            max_m3 = min(CAP_M3, sum(lines[i].m3 for i, _ in eligible))
            nb_tiers = min(len(bands), bisect_left(bands, max_kg + 1e-8) + 1)
            day_bands = [min(cap, max_kg) for cap in bands[:nb_tiers]]
            tiers = [model.var(price, upper=len(eligible)) for price in prices[:nb_tiers]]
            model.row([(x, lines[i].kg) for i, x in eligible] +
                      [(v, -b) for b, v in zip(day_bands, tiers, strict=True)], high=0)
            model.row([(x, lines[i].m3) for i, x in eligible] +
                      [(v, -max_m3) for v in tiers], high=0)
            model.row([(v, 1) for v in tiers] + [(y, -len(eligible))], high=0)
            model.row([(v, 1) for v in tiers] + [(y, -1)], low=0)
            # Each assigned line needs an actual truck with a sufficiently high tier.
            # Lines filling more than half a dimension always need distinct trucks.
            for i, x in eligible:
                first_tier = bisect_left(bands, lines[i].kg)
                if first_tier:
                    model.row([(v, 1) for v in tiers[first_tier:]] + [(x, -1)], low=0)
            for axis, half in (("kg", CAP_KG / 2), ("m3", CAP_M3 / 2)):
                large = [(lines[i].kg, x) for i, x in eligible if getattr(lines[i], axis) > half]
                for k in {bisect_left(bands, kg) for kg, _ in large}:
                    model.row([(v, 1) for v in tiers[k:]] +
                              [(x, -1) for kg, x in large if bisect_left(bands, kg) >= k], low=0)
        day_costs[j] = [*(x for _, x in direct), *range(start_columns, len(model.costs))]

    if window_cuts:
        window_floor = add_window_cost_cuts(model, day_costs, by_line, lines, bands, prices, tight_windows)
        prior_lower = max(prior_lower or 0, window_floor)
    if fractional:
        model.continuous.update(x for choices in by_line for _, x in choices)
        model.continuous.update(x for slots in truck_slots.values() for pieces in slots for _, x in pieces)
        exact = False
    weeks = defaultdict(dict)
    for j, day in enumerate(workdays):
        weeks[day - timedelta(days=day.weekday())][day.weekday()] = used_days.get(j)
    witnesses = []
    for week in weeks.values():
        h = model.var()
        witnesses.append(h)
        for w in range(5):
            # A selected weekday must have a real departure in the witness week.
            y = week.get(w)
            model.row([(h, 1), (weekdays[w], 1)] + ([] if y is None else [(y, -1)]), high=1)
    model.row([(h, 1) for h in witnesses], low=1)
    model.row([(s, 1) for s in weekdays], low=1)

    def rate(kg):
        return prices[bisect_left(bands, kg)]

    def group_rate(group):
        if compact_joint:
            return prices[bisect_left(gram_bands, sum(grams[i] for i in group))]
        return rate(sum(lines[i].kg for i in group))

    def feasible(trucks, profile):
        seen = []
        regular = defaultdict(set)
        first_day = min((d for d in workdays if d >= anchor and d.weekday() in profile), default=None)
        for day, group in trucks:
            kg = sum(lines[i].kg for i in group)
            m3 = sum(lines[i].m3 for i in group)
            if not group or kg > CAP_KG + 1e-8 or m3 > CAP_M3 + 1e-8 or day not in workdays:
                return False
            if day.weekday() in profile:
                regular[day - timedelta(days=day.weekday())].add(day.weekday())
            elif day < anchor or (first_day is not None and any(lines[i].due >= first_day for i in group)):
                return False
            if any(not 0 <= (lines[i].due - day).days <= 7 for i in group):
                return False
            seen.extend(group)
        return sorted(seen) == list(range(len(lines))) and set(profile) in regular.values()

    def pack_dates(by_day):
        return [(t.day, tuple(positions[l.id] for l in t.lines)) for t in pack(by_day)]

    def score(trucks):
        return (sum(group_rate(ids) for _, ids in trucks), len(trucks),
                sum((lines[i].due - d).days for d, ids in trucks for i in ids))

    seeds = []
    for w in range(5):
        assignments = defaultdict(list)
        for line in lines:
            candidates = [d for d in workdays if 0 <= (line.due - d).days <= 7
                          and (d.weekday() == w or (d >= anchor and (first[w] is None or line.due < first[w])))]
            if not candidates:
                break
            assignments[max(candidates)].append(line)
        if sum(map(len, assignments.values())) == len(lines):
            seed = pack_dates(assignments)
            if branch_profile in (None, (w,)) and feasible(seed, (w,)):
                seeds.append((seed, (w,), "profil_simple"))
    for name, pattern, actual in published:
        mapped = [(d, tuple(positions[i] for i in ids if i in positions)) for d, ids in actual]
        mapped = [(d, ids) for d, ids in mapped if ids]
        if pattern and branch_profile in (None, pattern) and feasible(mapped, pattern):
            seeds.append((mapped, pattern, name))
    objective = [(i, c) for i, c in enumerate(model.costs) if c]
    if prior_lower is not None:
        model.row(objective, low=prior_lower)
    cutoff = branch_upper
    if seeds:
        seed_cost = min(score(plan) for plan, _, _ in seeds)[0]
        cutoff = seed_cost if cutoff is None else min(seed_cost, cutoff)
    if cutoff is not None:
        model.row(objective, high=cutoff - int(strict_cutoff))

    answer = model.solve(None if enumerate_loads else bound_seconds)
    if answer.x is not None and not fractional:
        profile = tuple(w for w, s in enumerate(weekdays) if answer.x[s] > 0.5)
        if enumerate_loads:
            solved = [(workdays[j], group) for j, group, x in truck_columns if answer.x[x] > 0.5]
        elif joint:
            solved = [(workdays[j], (i,)) for j, i, x in direct_trucks if answer.x[x] > .5]
            for j, slots in truck_slots.items():
                secondary = set()
                for pieces in slots:
                    group = tuple(i for i, x in pieces if answer.x[x] > 0.5)
                    if group:
                        solved.append((workdays[j], group))
                        secondary.update(group)
                if j in compact_primary:
                    group = tuple(i for i, x in compact_primary[j] if answer.x[x] > 0.5 and i not in secondary)
                    if group:
                        solved.append((workdays[j], group))
        else:
            assignments = defaultdict(list)
            for i, possible in enumerate(by_line):
                selected = [workdays[j] for j, x in possible if answer.x[x] > 0.5]
                if len(selected) != 1:
                    raise ValueError("Solver line assignment is not integral")
                assignments[selected[0]].append(lines[i])
            solved = pack_dates(assignments)
        if not feasible(solved, profile):
            raise ValueError("Solver plan violates the common rules")
        seeds.append((solved, profile, "camions_exacts" if exact else "borne_rechargee"))
    dual = getattr(answer, "mip_dual_bound", None)
    lower = max(0, int(np.ceil(dual - 1e-5))) if dual is not None and np.isfinite(dual) else 0
    if strict_cutoff and cutoff is not None and answer.status == 2:
        lower = cutoff
    if prior_lower is not None:
        lower = max(prior_lower, lower)
    if not seeds and branch_profile is not None:
        return dict(lower=lower, upper=None), []
    if not seeds:
        raise ValueError(f"No feasible plan for {scope} {lines[0].supplier} {lines[0].plant}: {answer.message}")
    trucks, profile, method = min(seeds, key=lambda p: score(p[0]))
    upper = score(trucks)[0]
    # Ignore the calendar and pack all remaining needs on one hypothetical day.
    # A line filling either dimension is alone even in this deliberately loose bound.
    full = [line for line in lines if line.kg >= CAP_KG or line.m3 >= CAP_M3]
    others = [line for line in lines if line.kg < CAP_KG and line.m3 < CAP_M3]
    forced = sum(rate(line.kg) for line in full)
    total_kg = sum(line.kg for line in others)
    total_m3 = sum(line.m3 for line in others)
    floor = max(total_kg * min(p / cap for p, cap in zip(prices, bands, strict=True)),
                total_m3 * min(prices) / CAP_M3,
                np.ceil(max(total_kg / CAP_KG, total_m3 / CAP_M3) - 1e-10) * min(prices))
    lower = max(lower, forced + int(np.ceil(floor - 1e-5)))
    if lower > upper:
        raise ValueError("Lower bound exceeds feasible cost")
    baseline = defaultdict(list)
    for line in lines:
        baseline[line.due].append(line)
    return (
        dict(scope=scope, supplier_id=lines[0].supplier, plant=lines[0].plant, lines=len(lines),
             method=method, exact_model=exact, solver_status=int(answer.status), lower=lower, upper=upper,
             reference=score(pack_dates(baseline))[0], trucks=len(trucks), certified=lower == upper,
             profile=" ".join(map(str, profile)),
             early_trucks=sum(d < anchor for d, _ in trucks),
             early_lines=sum(len(ids) for d, ids in trucks if d < anchor)),
        [(d, [lines[i].id for i in ids], group_rate(ids)) for d, ids in trucks],
    )


PROFILES = tuple(profile for count in range(1, 6) for profile in combinations(range(5), count))


def combine_profiles(results):
    if set(results) != set(PROFILES):
        raise ValueError("Some weekday profiles were not evaluated")
    plans = [results[p] for p in PROFILES if results[p][0]["upper"] is not None]
    if not plans:
        raise ValueError("No profile retained the incumbent")
    best, trucks = min(plans, key=lambda result: (result[0]["upper"], result[0]["trucks"]))
    stats = best.copy()
    bounds = {p: results[p][0]["lower"] for p in PROFILES}
    stats["lower"] = min(bounds.values())
    if stats["lower"] > stats["upper"]:
        raise ValueError("Profile bounds exceed the best feasible cost")
    stats["certified"] = stats["lower"] == stats["upper"]
    stats["solver_status"] = 0 if stats["certified"] else 1
    stats["open_profiles"] = " / ".join("".join(map(str, p)) for p, bound in bounds.items() if bound < stats["upper"])
    return stats, trucks


def profile_job(job, profile, upper):
    return (*job[:15], profile, upper, *job[16:])


def solve_profile_partition(job):
    upper = job[15]
    results = {}
    for profile in PROFILES:
        stats, trucks = results[profile] = solve_pair(profile_job(job, profile, upper))
        if stats["upper"] is not None:
            upper = min(upper, stats["upper"])
    return combine_profiles(results)


def parallel_profile_results(pool, jobs, scopes, output):
    futures = {
        pool.submit(solve_pair, profile_job(job, profile, job[15])): (i, profile)
        for profile in PROFILES for i, job in enumerate(jobs)
    }
    print(f"{len(futures)} profile problems scheduled across {len(jobs)} distinct inputs", flush=True)
    grouped = [{} for _ in jobs]
    audit = []
    for finished, future in enumerate(as_completed(futures), 1):
        i, profile = futures[future]
        result = grouped[i][profile] = future.result()
        line = jobs[i][1][0]
        for scope in scopes[i]:
            audit.append(dict(scope=scope, supplier_id=line.supplier, plant=line.plant,
                              profile=" ".join(map(str, profile)), lower=result[0]["lower"],
                              upper=result[0]["upper"], input_cutoff=jobs[i][15]))
        if finished % 60 == 0 or finished == len(futures):
            print(f"{finished}/{len(futures)} profile problems evaluated", flush=True)
    pd.DataFrame(audit).sort_values(["scope", *KEY, "profile"]).to_csv(output / "profils.csv", index=False)
    return [combine_profiles(results) for results in grouped]


def run(repo, output, workers, exact_size, bound_seconds, joint=False, couples=None, wissam_only=False,
        earliest=None, incumbent=None, compact_joint=False, open_from=None, adaptive=False, window_cuts=False,
        strict_cutoff=False, split_profiles=False, fractional=False, tight_windows=False, parallel_profiles=False):
    if parallel_profiles and not split_profiles:
        raise ValueError("--parallel-profiles requires --split-profiles")
    def read(path, ref=REF):
        blob = subprocess.check_output([
            "git", "-C", str(repo), "-c", f"safe.directory={repo.resolve().as_posix()}",
            "show", f"{ref}:{path}",
        ])
        return pd.read_csv(io.BytesIO(blob))

    prepared = read("data/consolidated_dataset_max.csv").drop(columns="rate_cost", errors="ignore")
    raw = read("docs/released_schedule.csv")
    raw["charge_inconnue"] = raw[["weight", "volume"]].fillna(0).le(0).any(axis=1)
    prepared = prepared.merge(raw[ROW + ["charge_inconnue"]], on=ROW, validate="1:1")
    distances = read("docs/distances.csv")
    known = distances.loc[distances.distance.gt(0), "supplier_id"]
    prepared["distance_inconnue"] = ~prepared.supplier_id.isin(known)
    tariff = read("docs/rate_sheet.csv").set_index("km_up_to")
    upper = lambda text: np.inf if str(text).startswith(">") else float(text)
    tariff.index, tariff.columns = tariff.index.map(upper), tariff.columns.map(upper)
    tariff = tariff.sort_index().sort_index(axis=1)
    bands = (*list(tariff.columns[:-1]), CAP_KG)
    af = read("data/candidat_max_sortie2_affectations.csv").rename(columns={"need_date": "date"})
    snapshots = [("wissam", af)]
    for name, filename in (
        ("prime_rg06", "ensemble-final/rg06-affectations.csv"),
        ("prime_rg05_rg06", "ensemble-cible/affectations.csv"),
    ):
        if wissam_only:
            break
        snapshot = read(f"milestone2/planification_testbed/{filename}", "416db4f").rename(columns={
            "date": "delivery_date", "due": "date", "supplier": "supplier_id", "dock": "unloading_point",
        })
        snapshot["profile_day"] = snapshot["delivery_date"]
        snapshots.append((name, snapshot))
    af_by_pair = defaultdict(list)
    for name, snapshot in snapshots:
        match = prepared[ROW].reset_index(names="prepared_id").merge(
            snapshot[ROW + ["delivery_date", "profile_day", "truck_id"]], on=ROW, validate="1:1",
        )
        if len(match) != len(prepared):
            raise ValueError(f"Different demand lines in {name}")
        for key, group in match.groupby(KEY):
            prof = tuple(sorted(set(pd.to_datetime(group.profile_day.dropna()).dt.dayofweek)))
            trucks = [(pd.Timestamp(day).date(), tuple(t.prepared_id.astype(str)))
                      for (day, _), t in group.groupby(["delivery_date", "truck_id"])]
            af_by_pair[key].append((name, prof, trucks))
    anchor, end = pd.Timestamp(prepared.date.min()).date(), pd.Timestamp(prepared.date.max()).date()
    start = max(earliest or anchor, anchor - timedelta(days=7))
    if start > anchor:
        raise ValueError("The comparison only lifts the original lower limit")
    holidays = german_national_holidays({start.year, end.year})
    workdays = tuple(d.date() for d in pd.date_range(start, end) if d.weekday() < 5 and d.date() not in holidays)
    prior = {}
    if incumbent is not None:
        prior_aff = pd.read_csv(incumbent, dtype={"prepared_id": str, "profile": str})
        columns = {name: f"{name}_prepared" for name in KEY}
        prior_aff = prior_aff.merge(prepared[ROW].reset_index(names="source_index").assign(
            prepared_id=lambda d: d.source_index.astype(str)).rename(
                columns={**columns, "date": "need_prepared"}),
            on="prepared_id", how="left", validate="m:1")
        if not (prior_aff["supplier_id"].eq(prior_aff["supplier_id_prepared"]).all()
                and prior_aff["plant"].eq(prior_aff["plant_prepared"]).all()
                and prior_aff["besoin"].eq(prior_aff["need_prepared"]).all()):
            raise ValueError("Incumbent lines do not match the prepared data")
        for key, group in prior_aff.groupby(["scope", *KEY]):
            trucks = [(pd.Timestamp(d).date(), tuple(t.prepared_id))
                      for (d, _), t in group.groupby(["date", "truck"])]
            prior[key] = ("incumbent_verifie", tuple(map(int, group.profile.iloc[0].split())), trucks)
    pending = None
    lower_by_key = {}
    upper_by_key = {}
    if split_profiles and (open_from is None or incumbent is None or not strict_cutoff):
        raise ValueError("Profile partition needs prior bounds, a feasible incumbent and --prove-better")
    if open_from is not None:
        before = pd.read_csv(open_from)
        pending = set(before.loc[before.lower.lt(before.upper), ["scope", *KEY]].itertuples(index=False, name=None))
        if couples:
            pending = {key for key in pending if ":".join(key[1:]) in couples}
        if strict_cutoff:
            lower_by_key = before.set_index(["scope", *KEY]).lower.astype(int).to_dict()
        if split_profiles:
            upper_by_key = before.set_index(["scope", *KEY]).upper.astype(int).to_dict()
    jobs = []
    for name, table in (
        ("avec_imputation", prepared), ("sans_imputation_tarifable",
                                        prepared.loc[~(prepared.charge_inconnue | prepared.distance_inconnue)]),
    ):
        for key, group in table.groupby(KEY):
            if (couples and ":".join(key) not in couples) or (pending is not None and (name, *key) not in pending):
                continue
            lines = [Line(str(i), r.supplier_id, r.plant, r.unloading_point, r.material, pd.Timestamp(r.date).date(),
                          r.schedule_quantity, r.weight, r.volume, r.distance)
                     for i, r in zip(group.index, group.itertuples(index=False), strict=True)]
            prices = tuple(round(100 * p) for p in tariff.iloc[bisect_left(tariff.index, lines[0].km)])
            seeds = af_by_pair[key].copy()
            if (name, *key) in prior:
                seeds.append(prior[(name, *key)])
            job = (name, lines, workdays, bands, prices, seeds,
                   exact_size, bound_seconds, joint, anchor, compact_joint or adaptive, adaptive, window_cuts,
                   strict_cutoff, lower_by_key.get((name, *key)))
            jobs.append((*job, upper_by_key[(name, *key)], fractional, tight_windows) if split_profiles
                        else (*job, None, None, fractional, tight_windows))
    jobs.sort(key=lambda job: -len(job[1]))
    if pending is not None and pending != {(job[0], job[1][0].supplier, job[1][0].plant) for job in jobs}:
        raise ValueError("The requested open pairs do not match the prepared inputs")
    if not jobs:
        raise ValueError("No couple matches the requested filters")
    output.mkdir(parents=True, exist_ok=True)
    summary, allocations = [], []
    factor = BaselineConfig().price_factor / 100
    unique_jobs, scopes = [], []
    by_lines = {}
    for job in jobs:
        scope, lines = job[:2]
        fingerprint = tuple(line.id for line in lines)
        if (compact_joint or adaptive) and fingerprint in by_lines:
            index = by_lines[fingerprint]
            scopes[index].append(scope)
            other = unique_jobs[index]
            other[5].extend(job[5])
            low = 14
            known = [bound for bound in (other[low], job[low]) if bound is not None]
            unique_jobs[index] = (*other[:low], max(known, default=None))
            if split_profiles:
                unique_jobs[index] += (min(other[15], job[15]), fractional, tight_windows)
            else:
                unique_jobs[index] += (None, None, fractional, tight_windows)
        else:
            by_lines[fingerprint] = len(unique_jobs)
            unique_jobs.append(job)
            scopes.append([scope])
    if compact_joint or adaptive:
        print(f"{len(unique_jobs)} independent models for {len(jobs)} pair/scope combinations", flush=True)
    solver = solve_profile_partition if split_profiles else solve_pair
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = (parallel_profile_results(pool, unique_jobs, scopes, output) if parallel_profiles
                   else pool.map(solver, unique_jobs))
        for (stats, trucks), names in zip(results, scopes, strict=True):
            for scope in names:
                summary.append({**stats, "scope": scope})
                for i, (day, ids, cost) in enumerate(trucks, 1):
                    for id_ in ids:
                        allocations.append(dict(scope=scope, supplier_id=stats["supplier_id"], plant=stats["plant"],
                                                date=day, truck=i, prepared_id=id_, cost_truck_eur=cost*factor))
            if len(summary) % 30 == 0:
                print(f"{len(summary)}/{len(jobs)} couples", flush=True)
            pd.DataFrame(summary).to_csv(output / "couples.csv", index=False)
    allocation = pd.DataFrame(allocations).merge(prepared.reset_index(names="prepared_id").assign(
        prepared_id=lambda d: d.prepared_id.astype(str)).rename(columns={"supplier_id": "supplier_source",
                                                                          "plant": "plant_source",
                                                                          "date": "besoin"}),
                                    on="prepared_id", validate="m:1")
    trucks = allocation.groupby(["scope", *KEY, "date", "truck"], as_index=False).agg(
        lines=("prepared_id", "size"), weight=("weight", "sum"), volume=("volume", "sum"),
        cost_eur=("cost_truck_eur", "first"),
    )
    if (allocation.duplicated(["scope", "prepared_id"]).any()
            or trucks.weight.gt(CAP_KG + 1e-8).any() or trucks.volume.gt(CAP_M3 + 1e-8).any()
            or not (pd.to_datetime(allocation.besoin) - pd.to_datetime(allocation.date)).dt.days.between(0, 7).all()):
        raise ValueError("Invalid exported plan")
    allocation.drop(columns="cost_truck_eur").to_csv(output / "affectations.csv", index=False)
    trucks.to_csv(output / "camions.csv", index=False)
    prepared.loc[prepared.charge_inconnue | prepared.distance_inconnue,
                 ROW + ["charge_inconnue", "distance_inconnue"]].to_csv(
        output / "exclusions_sans_imputation.csv", index=False,
    )
    frame = pd.DataFrame(summary)
    report = {}
    for name, group in frame.groupby("scope"):
        a, b, c = (float(group[k].sum()) * factor for k in ("reference", "lower", "upper"))
        report[name] = dict(lines=int(group.lines.sum()), groups=len(group), certified=int(group.certified.sum()),
                            exact_models=int(group.exact_model.sum()), trucks=int(group.trucks.sum()),
                            earliest_allowed=start.isoformat(), early_trucks=int(group.early_trucks.sum()),
                            early_lines=int(group.early_lines.sum()),
                            reference_eur=round(a, 2), lower_eur=round(b, 2), cost_eur=round(c, 2),
                            saving_eur=round(a-c, 2), gap_eur=round(c-b, 2))
    (output / "bilan.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("results/optimisation-cout"))
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--exact-size", type=int, default=9)
    parser.add_argument("--bound-seconds", type=float, default=5)
    parser.add_argument("--joint", action="store_true", help="Model each physical truck even for large groups")
    parser.add_argument("--compact-joint", action="store_true", help="Prune dominated tariff patterns on days that fit one truck")
    parser.add_argument("--adaptive", action="store_true", help="Use the compact model or a capacity relaxation on dense pairs")
    parser.add_argument("--open-from", type=Path, help="Solve only pairs and scopes with a gap in this couples.csv")
    parser.add_argument("--window-cuts", action="store_true", help="Add independently valid lower bounds on date intervals")
    parser.add_argument("--prove-better", action="store_true", help="Require a strict improvement and reuse the existing bound")
    parser.add_argument("--split-profiles", action="store_true", help="Prove bounds across the 31 weekday profiles separately")
    parser.add_argument("--parallel-profiles", action="store_true", help="Share a worker pool across pairs and profiles")
    parser.add_argument("--fractional-lines", action="store_true", help="Bound the cost by relaxing indivisible lines")
    parser.add_argument("--tight-window-cuts", action="store_true", help="Include local capacity and minimum departure counts")
    parser.add_argument("--couple", action="append", help="Only solve SUP_xxx:PLT_xxx (repeatable)")
    parser.add_argument("--wissam-only", action="store_true", help="Use only Wissam's published plan as external seed")
    parser.add_argument("--earliest", type=date.fromisoformat, help="Allow regular fixed-profile days before the first need")
    parser.add_argument("--incumbent", type=Path, help="Reuse a verified export for both scopes")
    args = parser.parse_args()
    run(args.repo, args.output, args.workers, args.exact_size, args.bound_seconds, args.joint, args.couple,
        args.wissam_only, args.earliest, args.incumbent, args.compact_joint, args.open_from, args.adaptive,
        args.window_cuts or args.tight_window_cuts, args.prove_better, args.split_profiles,
        args.fractional_lines, args.tight_window_cuts, args.parallel_profiles)
