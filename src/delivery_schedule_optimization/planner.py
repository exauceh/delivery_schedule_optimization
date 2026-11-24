"""Delivery planner (Milestone 3): a fixed set of delivery days per supplier x plant couple.

Adapted from `fast_plan.py` on the `prime` branch (line-level truck packing, 7-day window,
per-truck pricing, automatic checks) with these corrections:

1. Scenario B (supplier x plant) is the official scenario. The hub variant, where one truck
   serves several plants, breaks RG01 and is not produced.
2. Profile (RG05, business answer): every couple delivers on a FIXED SET of weekdays. Each week it
   may use any part of this set (Mon+Tue+Wed one week, Tue+Wed the next, Mon+Wed the next), never
   another day. The profile is the largest number of deliveries made in one week over the period,
   and it must equal the number of days in the set ("3 2 1 1 1" = profile 3 on 3 fixed days).
3. Every line of the consolidated dataset is planned, imputed loads included (RG08).
4. Delivery days are spread between the couples of each plant (RG09), without changing the
   profile chosen by RG07.
5. Nothing ships before the schedule is released (start of the plan).

Optimisation (exact):
- per couple and set of days, the departure days are chosen by dynamic programming. With RG06 (a delivery
  day covers the needs up to the next delivery day, excluded) a plan is defined by its departure days, and
  its cost is a sum over consecutive departures: the best plan is a shortest path, as in dynamic lot
  sizing with demand time windows (Lee, Cetinkaya & Wagelmans, Management Science 2001);
- smoothing (RG09) chooses one plan per couple for all the couples of a plant together, as an integer
  linear programme solved to optimality (HiGHS), like the choice of visit-day combinations in periodic
  routing or of delivery patterns in retail (Sternbeck & Kuhn 2014).

Choice of the profile (RG07 + client answer "add delivery days rather than trucks"): the lowest
profile whose departures each fit in one truck; a truck holding a single line completed with a full
truck (unknown weight) is not counted as an overflow, since no profile can avoid it. If even 5 days
overflow, the profile with the fewest overflowing departures is kept and trucks are added.
"""
from __future__ import annotations

import itertools
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import holidays as holiday_calendars
import numpy as np
import pandas as pd

DAY_NAMES = ("lundi", "mardi", "mercredi", "jeudi", "vendredi")
COUPLE = ["supplier_id", "plant"]


@dataclass(frozen=True)
class PlannerConfig:
    """All tunable parameters of the planner (EF12)."""

    truck_kg: float = 24_500.0  # RG04
    truck_m3: float = 110.4  # RG04
    margin: float = 2.3923  # RG11: freight value margin (239.23 %)
    diesel_surcharge: float = 1.1943  # RG11
    max_advance_days: int = 7  # RG03: calendar days
    allowed_frequencies: tuple[int, ...] = (1, 2, 3, 4, 5)  # RG05: profile = deliveries per week (max); 4 allowed
    holiday_country: str = "DE"  # RG10: no reception on public holidays
    # RG09 smoothing of the delivery days per plant
    smoothing: bool = True  # False = keep the RG07 choice of every couple (no smoothing)
    # intensity: extra cost accepted for smoothing, share of the cost without smoothing (0.01 = 1 %). With 0, the
    # planner only spreads the days among plans of the same cost (smoothing for free).
    smoothing_budget: float = 0.0
    smoothing_time_limit: float = 60.0  # seconds given to the solver
    objective: str = "cost"  # "cost": cheapest plan first; "trucks": fewest trucks first, then cost
    # Profile = largest number of deliveries in one week ("3 2 1 1 2 1" = profile 3). True = also prefer the
    # lowest profile before the cost (former RG07 reading); False = the profile is the result of the best plan.
    lowest_profile_first: bool = False
    # RG06 (coverage). False (default) = a need may leave with ANY planned departure of its couple within its
    # window [date - max_advance_days, date]; True = only with the latest departure on or before its date.
    strict_coverage: bool = False
    reassign_passes: int = 50  # improvement passes of the assignment when strict_coverage is False
    workers: int | None = None  # processes for the candidate plans (None = every CPU core, 1 = no parallelism)
    # Client answer "add delivery days rather than trucks": True = fewest overflowing departures before the
    # cost; False = the cost decides alone (a departure may take a second truck if this is cheaper).
    overflow_first: bool = False
    improvement_passes: int = 20  # greedy reference planner (`plan_couple`) only
    # First possible delivery day: nothing ships before the schedule is released. None = date of the first need.
    plan_start: str | None = None
    # Needs with no profile day on or after plan_start in their window: "need_day" = delivered on their own
    # date (as today), "first_day" = grouped on the first day of the plan.
    startup_delivery: str = "need_day"

    @property
    def price_factor(self) -> float:
        return (1 + self.margin) * self.diesel_surcharge


# --------------------------------------------------------------------------- inputs
def load_lines(path: Path, config: PlannerConfig) -> pd.DataFrame:
    """Load the consolidated dataset: one line = one need (date, supplier, material, plant, dock)."""
    lines = pd.read_csv(path, parse_dates=["date"]).drop(columns="rate_cost", errors="ignore")
    required = {"date", "supplier_id", "material", "plant", "unloading_point",
                "schedule_quantity", "weight", "volume", "distance"}
    if missing := required.difference(lines.columns):
        raise ValueError(f"consolidated dataset misses columns: {sorted(missing)}")
    if lines[["weight", "volume", "distance"]].isna().any().any() or (lines[["weight", "volume"]] <= 0).any().any():
        raise ValueError("unresolved or non-positive load / distance")
    if ((lines["weight"] > config.truck_kg) | (lines["volume"] > config.truck_m3)).any():
        raise ValueError("a single line is larger than one truck")
    if lines.groupby("supplier_id")["distance"].nunique().gt(1).any():
        raise ValueError("a supplier has several distances")
    lines = lines.sort_values(["supplier_id", "plant", "date", "unloading_point", "material"]).reset_index(drop=True)
    lines.insert(0, "line_id", np.arange(len(lines)))
    # Lines completed with a full truck by the preparation step (weight or volume unknown)
    lines["imputed_full_truck"] = (lines["weight"] == config.truck_kg) | (lines["volume"] == config.truck_m3)
    return lines


def load_rate_grid(path: Path) -> pd.DataFrame:
    """Wide rate sheet (docs/rate_sheet.csv) -> grid indexed by km bound, columns kg bound (inf = open)."""
    raw = pd.read_csv(path, dtype={"km_up_to": str}).set_index("km_up_to")
    to_bound = lambda text: np.inf if str(text).strip().startswith(">") else float(text)
    grid = raw.astype(float)
    grid.index, grid.columns = grid.index.map(to_bound), grid.columns.map(to_bound)
    return grid.sort_index().sort_index(axis=1)


class Tariff:
    """Price of ONE truck: grid(distance bracket, truck weight bracket) x (1 + margin) x diesel (RG11)."""

    def __init__(self, grid: pd.DataFrame, config: PlannerConfig):
        self.km = grid.index.to_numpy(dtype=float)
        self.kg = grid.columns.to_numpy(dtype=float)
        self.prices = grid.to_numpy(dtype=float) * config.price_factor

    def __call__(self, distance_km, weight_kg) -> np.ndarray:
        row = np.searchsorted(self.km, np.asarray(distance_km, dtype=float), side="left")
        col = np.searchsorted(self.kg, np.asarray(weight_kg, dtype=float), side="left")
        return self.prices[row, col]


def public_holidays(lines: pd.DataFrame, config: PlannerConfig) -> set[date]:
    years = range(lines["date"].min().year - 1, lines["date"].max().year + 1)
    return set(holiday_calendars.country_holidays(config.holiday_country, years=years))


def day_sets(config: PlannerConfig) -> list[tuple[int, ...]]:
    """Every set of weekdays whose size is an allowed profile (31 sets for 1..5)."""
    return [p for k in sorted(config.allowed_frequencies) for p in itertools.combinations(range(5), k)]


def plan_start(lines: pd.DataFrame, config: PlannerConfig) -> date:
    return pd.Timestamp(config.plan_start).date() if config.plan_start else lines["date"].min().date()


def is_working_day(day: date, holidays: set[date]) -> bool:
    return day.weekday() < 5 and day not in holidays


def first_working_day(start: date, holidays: set[date]) -> date:
    day = start
    while not is_working_day(day, holidays):
        day += timedelta(days=1)
    return day


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


# --------------------------------------------------------------------------- delivery options of a need
def delivery_options(day: date, days: tuple[int, ...], holidays: set[date], max_advance: int,
                     first_day: date | None, startup: str) -> list[tuple[date | None, date]]:
    """Possible (profile day, delivery date) pairs for a need dated `day`, latest first.

    - a profile day = a day of the set within [day - max_advance, day] (RG03, RG05);
    - RG10: a profile day that is a public holiday is delivered on the previous working day, still
      within the window;
    - nothing before the first day of the plan. A need with no option left is a start-up delivery,
      on its own date ("need_day") or on the first day ("first_day"), shown with profile day None.
    """
    earliest = day - timedelta(days=max_advance)
    options, seen = [], set()
    for advance in range(max_advance + 1):
        slot = day - timedelta(days=advance)
        if slot.weekday() not in days:
            continue
        delivery = slot
        if slot in holidays:  # RG10
            delivery = next((slot - timedelta(days=a) for a in range(1, (slot - earliest).days + 1)
                             if is_working_day(slot - timedelta(days=a), holidays)), None)
        if delivery is None or (first_day and delivery < first_day) or delivery in seen:
            continue
        seen.add(delivery)
        options.append((slot, delivery))
    if not options and first_day and first_day <= day < first_day + timedelta(days=7):
        options.append((None, day if startup == "need_day" else first_day))
    return options


def delivery_dates(due: np.ndarray, days: tuple[int, ...], holidays: set[date], max_advance: int,
                   start: date | None = None, startup: str = "need_day") -> np.ndarray | None:
    """Latest possible delivery date of every need (RG06 starting point), or None if a need is uncovered."""
    first_day = first_working_day(start, holidays) if start else None
    out = []
    for d in due:
        options = delivery_options(pd.Timestamp(d).date(), days, holidays, max_advance, first_day, startup)
        if not options:
            return None
        out.append(options[0][1])
    return np.array(out, dtype="datetime64[D]")


# --------------------------------------------------------------------------- packing and evaluation
def pack(weight: np.ndarray, volume: np.ndarray, config: PlannerConfig) -> list[list[int]]:
    """First-fit decreasing on the binding ratio: whole lines go to the first truck where they fit (RG04)."""
    order = np.lexsort((-volume, -weight, -np.maximum(weight / config.truck_kg, volume / config.truck_m3)))
    trucks: list[list[int]] = []
    loads: list[list[float]] = []
    for i in order:
        for t, (kg, m3) in enumerate(loads):
            if kg + weight[i] <= config.truck_kg + 1e-9 and m3 + volume[i] <= config.truck_m3 + 1e-9:
                loads[t][0] += weight[i]
                loads[t][1] += volume[i]
                trucks[t].append(i)
                break
        else:
            loads.append([weight[i], volume[i]])
            trucks.append([i])
    return trucks


@dataclass
class CouplePlan:
    allowed_days: tuple[int, ...]  # set of days evaluated
    profile: tuple[int, ...]  # days actually used (= the fixed delivery days of the couple)
    slots: list[date | None]  # profile day served by each line (None = start-up delivery)
    dates: np.ndarray  # delivery date of each line of the couple
    trucks: list[tuple[np.datetime64, list[int]]]  # (date, positions of the couple's lines)
    truck_weights: np.ndarray
    cost: float
    overflow_departures: int  # departures whose MEASURED lines need more than one truck
    total_advance: int
    weekly_deliveries: dict[date, int]  # Monday -> number of profile days used that week

    @property
    def frequency(self) -> int:
        """Profile = largest number of deliveries made in one week over the period."""
        return max(self.weekly_deliveries.values(), default=0)

    @property
    def n_trucks(self) -> int:
        return len(self.trucks)

    @property
    def trucks_per_date(self) -> dict[np.datetime64, int]:
        count: dict[np.datetime64, int] = defaultdict(int)
        for d, _ in self.trucks:
            count[d] += 1
        return count

    @property
    def same_days(self) -> bool:
        """Business rule: deliveries always fall on the same set of days, whose size is the profile."""
        return len(self.profile) == self.frequency


def evaluate(lines: pd.DataFrame, options: list, choice: list[int], allowed_days: tuple[int, ...],
             tariff: Tariff, config: PlannerConfig) -> CouplePlan:
    """Pack every departure of a given assignment of the needs and price it."""
    weight, volume = lines["weight"].to_numpy(float), lines["volume"].to_numpy(float)
    imputed = lines["imputed_full_truck"].to_numpy(bool)
    picked = [options[i][c] for i, c in enumerate(choice)]
    slots = [slot for slot, _ in picked]
    dates = np.array([delivery for _, delivery in picked], dtype="datetime64[D]")
    trucks: list[tuple[np.datetime64, list[int]]] = []
    overflow = 0
    for day in np.unique(dates):
        idx = np.flatnonzero(dates == day)
        packed = pack(weight[idx], volume[idx], config)
        trucks += [(day, idx[t].tolist()) for t in packed]
        if len(packed) > 1:  # overflow of measured lines, or only full-truck imputations?
            measured = idx[~imputed[idx]]
            overflow += len(measured) > 0 and len(pack(weight[measured], volume[measured], config)) > 1
    truck_weights = np.array([weight[t].sum() for _, t in trucks])
    cost = float(tariff(np.full(len(trucks), lines["distance"].iloc[0]), truck_weights).sum())
    due = lines["date"].to_numpy(dtype="datetime64[D]")
    used = sorted({slot for slot in slots if slot is not None})
    weekly: dict[date, int] = defaultdict(int)
    for slot in used:
        weekly[monday(slot)] += 1
    return CouplePlan(
        allowed_days=allowed_days, profile=tuple(sorted({s.weekday() for s in used})), slots=slots, dates=dates,
        trucks=trucks, truck_weights=truck_weights, cost=cost, overflow_departures=int(overflow),
        total_advance=int((due - dates).astype(int).sum()), weekly_deliveries=dict(weekly),
    )


def evaluate_departures(lines: pd.DataFrame, departures: list[tuple[date | None, date]], allowed_days: tuple[int, ...],
                        tariff: Tariff, config: PlannerConfig) -> CouplePlan:
    """RG06: every need leaves with the latest departure on or before its date. `departures` = (profile day, date)."""
    departures = sorted(departures, key=lambda d: d[1])
    starts = np.array([d for _, d in departures], dtype="datetime64[D]")
    due = lines["date"].to_numpy(dtype="datetime64[D]")
    position = np.searchsorted(starts, due, side="right") - 1
    if (position < 0).any():
        raise ValueError("a need is before the first departure")
    options = [[departures[p]] for p in position]
    return evaluate(lines, options, [0] * len(options), allowed_days, tariff, config)


def plan_key(plan: CouplePlan) -> tuple:
    """Inside one set of days: valid profile first, then fewest overflows, cheapest, fewest trucks, least advance."""
    return (not plan.same_days, plan.overflow_departures, round(plan.cost, 6), plan.n_trucks, plan.total_advance)


def plan_couple(lines: pd.DataFrame, days: tuple[int, ...], holidays: set[date], tariff: Tariff,
                config: PlannerConfig, start: date | None = None, optimize: bool = True) -> CouplePlan | None:
    """Plan one couple on one set of days.

    Every need starts on its latest possible day (RG06). If `optimize`, the planner then tries to empty
    each departure by moving its needs to another departure already planned within their window,
    and keeps the move when the plan improves (`plan_key`). Returns None if a need cannot be covered
    or if the deliveries do not respect the "same days" rule.
    """
    first_day = first_working_day(start, holidays) if start else None
    by_date = {d: delivery_options(d, days, holidays, config.max_advance_days, first_day, config.startup_delivery)
               for d in lines["date"].dt.date.unique()}
    if any(not opts for opts in by_date.values()):
        return None
    options = [by_date[d] for d in lines["date"].dt.date]
    choice = [0] * len(options)
    best = evaluate(lines, options, choice, days, tariff, config)
    for _ in range(config.improvement_passes if optimize else 0):
        improved = False
        departures = sorted(np.unique(best.dates), key=lambda d: best.truck_weights[[i for i, (t, _) in enumerate(best.trucks) if t == d]].sum())
        for day in departures:
            others = set(best.dates.tolist()) - {pd.Timestamp(day).date()}
            trial = choice.copy()
            for i in np.flatnonzero(best.dates == day):
                alternative = next((j for j, (_, delivery) in enumerate(options[i]) if delivery in others), None)
                if alternative is None:
                    break
                trial[i] = alternative
            else:
                candidate = evaluate(lines, options, trial, days, tariff, config)
                if plan_key(candidate) < plan_key(best):
                    choice, best, improved = trial, candidate, True
                    break
        if not improved:
            break
    if optimize and (not best.same_days or best.frequency not in config.allowed_frequencies):
        return None
    return best


# --------------------------------------------------------------------------- exact plan of one couple
def departure_nodes(due: list[date], days: tuple[int, ...], holidays: set[date], first_day: date
                    ) -> list[tuple[date | None, date]]:
    """Possible departures of a couple on a set of days: (profile day, delivery date), by date.

    - every day of the set from the first day of the plan to the last need (RG05), a public holiday being
      delivered on the previous working day (RG10);
    - before the first profile day of the plan, any working day (start-up departure, profile day None):
      the needs dated before it have no profile day in their window.
    """
    last = max(due)
    nodes: dict[date, tuple[date | None, date]] = {}
    day = first_day
    while day <= last:
        if day.weekday() in days:
            delivery = day
            while not is_working_day(delivery, holidays):
                delivery -= timedelta(days=1)
            if delivery >= first_day:
                nodes.setdefault(delivery, (day, delivery))
        day += timedelta(days=1)
    first_profile = min(nodes, default=last + timedelta(days=1))
    day = first_day
    while day < first_profile and day <= last:
        if is_working_day(day, holidays):
            nodes.setdefault(day, (None, day))
        day += timedelta(days=1)
    return [nodes[d] for d in sorted(nodes)]


def optimal_plan(lines: pd.DataFrame, days: tuple[int, ...], holidays: set[date], tariff: Tariff,
                 config: PlannerConfig, start: date, startup_need_day: bool = False) -> CouplePlan | None:
    """Best plan of one couple on one set of days, by dynamic programming (exact).

    With RG06 a plan is fully defined by its departure days: each need leaves with the latest departure
    on or before its date, which must be at most `max_advance_days` before (RG03). The cost is then a sum
    over consecutive departures, so the best set of departures is a shortest path (dynamic lot sizing
    with demand time windows). Order of the criteria: overflowing departures (RG07, client answer), cost,
    trucks, advance. If the best path does not respect the "same days" rule, a second dynamic programme
    keeps track of the days used and of the busiest week.

    Start-up (needs dated before the first profile day of the plan): the programme chooses the start-up
    departures like the others (usually one); with `startup_need_day`, each start-up need leaves on its
    own date, as today (more departures, but none moved to the first day: an option for smoothing).
    """
    first_day = first_working_day(start, holidays)
    due_dates = lines["date"].dt.date.to_numpy()
    nodes = departure_nodes(list(due_dates), days, holidays, first_day)
    if not nodes or nodes[0][1] > min(due_dates):
        return None
    weight, volume = lines["weight"].to_numpy(float), lines["volume"].to_numpy(float)
    imputed = lines["imputed_full_truck"].to_numpy(bool)
    distance = float(lines["distance"].iloc[0])
    cache: dict[tuple[int, int | None], tuple | None] = {}
    startup_days = {d for s, d in nodes if s is None}
    first_profile = min((d for s, d in nodes if s is not None), default=date.max)
    EMPTY = "empty"  # a departure must carry at least one need

    def segment(i: int, j: int | None) -> tuple | str | None:
        """Criteria of the departure of node i, which serves the needs up to node j (excluded).
        None = forbidden (and so for every earlier node), EMPTY = nothing to carry."""
        if (i, j) not in cache:
            slot, delivery = nodes[i]
            end = nodes[j][1] if j is not None else date.max
            idx = np.flatnonzero((due_dates >= delivery) & (due_dates < end))
            if not len(idx):
                cache[i, j] = EMPTY
            elif (max(due_dates[idx]) - delivery).days > config.max_advance_days:
                cache[i, j] = None  # RG03
            elif slot is None and max(due_dates[idx]) >= first_profile:
                cache[i, j] = None  # a start-up departure only carries needs dated before the first profile day
            elif startup_need_day and any(d in startup_days and d != delivery for d in due_dates[idx]):
                cache[i, j] = None  # start-up need that must leave on its own date
            else:
                packed = pack(weight[idx], volume[idx], config)
                kg = np.array([weight[idx[t]].sum() for t in packed])
                over = 0
                if len(packed) > 1:
                    measured = idx[~imputed[idx]]
                    over = int(len(measured) > 0 and len(pack(weight[measured], volume[measured], config)) > 1)
                advance = sum((d - delivery).days for d in due_dates[idx])
                price = float(tariff(np.full(len(kg), distance), kg).sum())
                first = over if config.overflow_first else 0  # overflow is compared first only on demand
                cache[i, j] = ((first, len(kg), price, advance) if config.objective == "trucks"
                               else (first, price, len(kg), advance))
        return cache[i, j]

    def add(a, b):
        return tuple(x + y for x, y in zip(a, b))

    def solve(track: bool):
        """Shortest path; with `track`, the state also holds (days used, busiest week reached, week, count)."""
        full_mask = sum(1 << d for d in days)

        def step(state, node):
            slot = node[0]
            if not track or slot is None:
                return state
            mask, full, week, count = state
            count = count + 1 if monday(slot) == week else 1
            return (mask | 1 << slot.weekday(), full or count == len(days), monday(slot), count)

        empty = (0, len(days) == 1, None, 0) if track else ()
        best: list[dict] = [dict() for _ in nodes]
        for j, node in enumerate(nodes):
            if node[1] <= min(due_dates):  # first departure: no need before it
                state = step(empty, node)
                best[j].setdefault(state, ((0, 0.0, 0, 0), None))
            for i in range(j - 1, -1, -1):
                if not best[i]:
                    continue
                cost = segment(i, j)
                if cost is None:
                    break  # an earlier departure is even further from the needs
                if cost is EMPTY:
                    continue
                for state, (key, _) in best[i].items():
                    new_state, new_key = step(state, node), add(key, cost)
                    if new_state not in best[j] or new_key < best[j][new_state][0]:
                        best[j][new_state] = (new_key, (i, state))
        final = None
        for i in range(len(nodes)):
            cost = segment(i, None) if best[i] else None
            if cost is None or cost is EMPTY:
                continue
            for state, (key, _) in best[i].items():
                if track and (state[0] != full_mask or not state[1]):
                    continue
                if final is None or add(key, cost) < final[0]:
                    final = (add(key, cost), i, state)
        if final is None:
            return None
        _, i, state = final
        path = []
        while i is not None:
            path.append(nodes[i])
            i, state = best[i][state][1] or (None, None)
        return path

    for track in (False, True):
        path = solve(track)
        if path is None:
            return None
        plan = evaluate_departures(lines, path, days, tariff, config)
        if plan.same_days and plan.frequency in config.allowed_frequencies:
            return plan
    return None


def reassign(lines: pd.DataFrame, plan: CouplePlan, holidays: set[date], tariff: Tariff, config: PlannerConfig,
             start: date) -> CouplePlan:
    """RG06 relaxed: improve the assignment of the needs to the planned departures (local search).

    Each need may leave with any planned departure of its couple within [date - max_advance_days, date]
    (a start-up departure only takes needs dated before the first profile day). Three moves, kept when the
    objective strictly decreases:
    1. empty a whole departure into the others;
    2. move one line to another departure;
    3. only when 1 and 2 find nothing in a pass: move TWO lines of the same departure together to another
       departure open for both. A pair can cross a price bracket that neither line crosses alone
       (each single move costs more, the joint move costs less).
    A departure left empty disappears, only if the "same days" rule still holds.
    """
    due = lines["date"].dt.date.to_numpy()
    weight, volume = lines["weight"].to_numpy(float), lines["volume"].to_numpy(float)
    distance = float(lines["distance"].iloc[0])
    slot_of = {d: s for s, d in zip(plan.slots, plan.dates.tolist())}
    first_day = first_working_day(start, holidays)
    first_profile = min((d for s, d in departure_nodes([max(due)], plan.allowed_days, holidays, first_day) if s is not None),
                        default=date.max)
    options = [[d for d in sorted(slot_of) if 0 <= (t - d).days <= config.max_advance_days
                and (slot_of[d] is not None or t < first_profile)] for t in due]
    assign = list(plan.dates.tolist())
    members: dict[date, set[int]] = defaultdict(set)
    for i, d in enumerate(assign):
        members[d].add(i)

    def cost(idx: set[int]) -> float:
        if not idx:
            return 0.0
        pos = np.fromiter(idx, int)
        kg, m3 = weight[pos].sum(), volume[pos].sum()
        if kg <= config.truck_kg + 1e-9 and m3 <= config.truck_m3 + 1e-9:
            trucks, price = 1, float(tariff(distance, kg))
        else:
            packed = pack(weight[pos], volume[pos], config)
            trucks = len(packed)
            price = float(tariff(np.full(trucks, distance), [weight[pos[t]].sum() for t in packed]).sum())
        return trucks * 1e6 + price if config.objective == "trucks" else price + trucks * 1e-6

    current = {d: cost(m) for d, m in members.items()}

    def valid(new_assign) -> CouplePlan | None:
        candidate = evaluate(lines, [[(slot_of[d], d)] for d in new_assign], [0] * len(new_assign),
                             plan.allowed_days, tariff, config)
        ok = candidate.same_days and candidate.frequency in config.allowed_frequencies
        return candidate if ok else None

    allowed = [set(o) for o in options]

    def move_pair() -> bool:
        """Neighbourhood 3: first pair (i, j) of one departure whose joint move to another open departure,
        allowed for both, strictly lowers the cost of the two departures. Deterministic order: source date,
        destination date, line positions. Only the two departures concerned are re-priced."""
        nonlocal assign
        for src in sorted(d for d, m in members.items() if len(m) >= 2):
            src_lines = sorted(members[src])
            targets = sorted({d for i in src_lines for d in allowed[i] if d != src and members[d]})
            for dst in targets:
                movable = [i for i in src_lines if dst in allowed[i]]
                if len(movable) < 2:
                    continue
                before = current[src] + current[dst]
                for i, j in itertools.combinations(movable, 2):
                    after_src, after_dst = cost(members[src] - {i, j}), cost(members[dst] | {i, j})
                    if after_src + after_dst >= before - 1e-9:
                        continue
                    new_assign = assign.copy()
                    new_assign[i] = new_assign[j] = dst
                    if len(members[src]) == 2 and valid(new_assign) is None:
                        continue  # emptying the source would break the "same days" rule
                    members[src] -= {i, j}
                    members[dst] |= {i, j}
                    current[src], current[dst] = after_src, after_dst
                    assign = new_assign
                    logging.getLogger(__name__).debug("pair move %s -> %s, lines %s: %.6f -> %.6f",
                                                      src, dst, (i, j), before, after_src + after_dst)
                    return True
        return False

    for _ in range(config.reassign_passes):
        improved = False
        for d in sorted(members, key=lambda x: weight[list(members[x])].sum() if members[x] else 0):
            if not members[d]:
                continue
            trial = {k: set(v) for k, v in members.items()}
            moved = trial.pop(d)
            for i in sorted(moved, key=lambda i: -weight[i]):
                others = [o for o in options[i] if o != d and o in trial and trial[o]]
                if not others:
                    break
                best = min(others, key=lambda o: cost(trial[o] | {i}) - cost(trial[o]))
                trial[best].add(i)
            else:
                new_cost = {k: cost(v) for k, v in trial.items()}
                if sum(new_cost.values()) < sum(current.values()) - 1e-9:
                    new_assign = assign.copy()
                    for k, v in trial.items():
                        for i in v:
                            new_assign[i] = k
                    if valid(new_assign) is not None:
                        members, current, assign, improved = defaultdict(set, trial), new_cost, new_assign, True
        for i in range(len(due)):
            src = assign[i]
            for dst in options[i]:
                if dst == src or not members[dst]:
                    continue
                before = current[src] + current[dst]
                after_src, after_dst = cost(members[src] - {i}), cost(members[dst] | {i})
                if after_src + after_dst < before - 1e-9:
                    new_assign = assign.copy()
                    new_assign[i] = dst
                    if len(members[src]) == 1 and valid(new_assign) is None:
                        continue  # emptying this departure would break the "same days" rule
                    members[src].discard(i); members[dst].add(i)
                    current[src], current[dst] = after_src, after_dst
                    assign, improved, src = new_assign, True, dst
        if not improved:
            improved = move_pair()  # pairs only once single moves are exhausted
        if not improved:
            break
    result = valid(assign)
    better = result is not None and (
        (result.n_trucks, result.cost) < (plan.n_trucks, plan.cost - 1e-9) if config.objective == "trucks"
        else result.cost < plan.cost - 1e-9)
    return result if better else plan


# --------------------------------------------------------------------------- choice of the profiles
def candidate_plans(lines: pd.DataFrame, holidays: set[date], tariff: Tariff, config: PlannerConfig) -> dict:
    """Best valid plan of every couple on each set of days (exact dynamic programme), duplicates removed.

    With `lowest_profile_first`, profiles are evaluated from the lowest: once a profile has a plan whose
    departures all fit in one truck, the higher profiles are not needed (they would never be chosen).
    The couples are independent (RG01): they are spread over `workers` processes. The result does not
    depend on the number of processes.
    """
    start = plan_start(lines, config)
    groups = list(lines.groupby(COUPLE, sort=True))
    jobs = [(group, holidays, tariff, config, start) for _, group in groups]
    workers = min(config.workers or os.cpu_count() or 1, len(jobs))
    if workers <= 1:
        results = [couple_candidates(job) for job in jobs]
    else:
        # One maths thread per process: the parallelism is between processes, and each OpenBLAS thread pool
        # would otherwise reserve its own memory at start-up.
        threads = {name: os.environ.get(name) for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}
        os.environ.update({name: "1" for name in threads})
        try:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(couple_candidates, jobs, chunksize=max(1, len(jobs) // (4 * workers))))
        finally:
            for name, value in threads.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
    return {key: plans for (key, _), plans in zip(groups, results)}


def couple_candidates(job: tuple) -> list[CouplePlan]:
    """Candidate plans of ONE couple (top-level function, so that it can run in another process)."""
    group, holidays, tariff, config, start = job
    plans = {}
    for size in sorted(config.allowed_frequencies):
        for days in itertools.combinations(range(5), size):
            for need_day in (False, True):
                plan = optimal_plan(group, days, holidays, tariff, config, start, need_day)
                if plan is not None and not config.strict_coverage:
                    plan = reassign(group, plan, holidays, tariff, config, start)
                if plan is not None:
                    plans.setdefault((plan.profile, tuple(plan.dates.tolist())), plan)
        if config.lowest_profile_first and any(p.overflow_departures == 0 for p in plans.values()):
            break
    return list(plans.values())


def selection_key(plan: CouplePlan, config: PlannerConfig = PlannerConfig()) -> tuple:
    """Choice of the plan of a couple (RG07 + client answer "add delivery days rather than trucks"):
    fewest overflowing departures first; then, with `lowest_profile_first`, the lowest profile; then the
    objective (cheapest or fewest trucks); then least advance, then earliest days of the week.
    The profile itself is always the largest number of deliveries made in one week."""
    cost, trucks = round(plan.cost, 6), plan.n_trucks
    main = (trucks, cost) if config.objective == "trucks" else (cost, trucks)
    lowest = (plan.frequency,) if config.lowest_profile_first else ()
    overflow = (plan.overflow_departures,) if config.overflow_first else ()
    return (*overflow, *lowest, *main, plan.overflow_departures, plan.total_advance, plan.frequency, plan.profile)


def choose_profiles(candidates: dict, config: PlannerConfig = PlannerConfig()) -> dict:
    """Plan (and so profile) of each couple without smoothing (RG07)."""
    return {key: min(plans, key=lambda p: selection_key(p, config)) for key, plans in candidates.items()}


def smoothing_options(candidates: dict, chosen: dict, config: PlannerConfig = PlannerConfig()) -> dict:
    """Plans a couple may take when smoothing: same number of overflowing departures as its RG07 choice
    (and the same profile with `lowest_profile_first`), so that RG07 stays respected."""
    return {
        key: sorted([p for p in plans if (not config.overflow_first or p.overflow_departures == chosen[key].overflow_departures)
                     and (not config.lowest_profile_first or p.frequency == chosen[key].frequency)],
                    key=lambda p: selection_key(p, config))
        for key, plans in candidates.items()
    }


def smoothing_indicator(plans) -> int:
    """RG09 indicator of one plant: trucks received on the busiest day of each week, summed over the weeks,
    plus the busiest day of the whole period (lower = deliveries better spread over the week)."""
    load: dict[np.datetime64, int] = defaultdict(int)
    for plan in plans:
        for d, n in plan.trucks_per_date.items():
            load[d] += n
    weekly: dict[date, int] = defaultdict(int)
    for d, n in load.items():
        week = monday(pd.Timestamp(d).date())
        weekly[week] = max(weekly[week], n)
    return sum(weekly.values()) + max(weekly.values(), default=0)


def plant_frontier(options: dict, budget: float, config: PlannerConfig) -> list[tuple[int, float, dict]]:
    """Exact cost / smoothing frontier of ONE plant (integer linear programme, HiGHS).

    For each value L of the indicator, from the cheapest plan downwards: the cheapest choice of one plan
    per couple whose indicator is at most L. Stops when the extra cost exceeds `budget` or L is infeasible.
    Returns (indicator, cost, plans) for each level.
    """
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import lil_matrix

    variables = [(key, plan) for key in sorted(options) for plan in options[key]]
    weeks = sorted({monday(pd.Timestamp(d).date()) for _, plan in variables for d in plan.trucks_per_date})
    n_x = len(variables)
    n = n_x + len(weeks) + 1  # plans, peak of each week, peak of the period
    rows: list[tuple[dict[int, float], float, float]] = []
    for key in sorted(options):  # one plan per couple
        rows.append(({j: 1.0 for j, (k, _) in enumerate(variables) if k == key}, 1.0, 1.0))
    load: dict[np.datetime64, dict[int, float]] = defaultdict(dict)
    for j, (_, plan) in enumerate(variables):
        for d, trucks in plan.trucks_per_date.items():
            load[d][j] = trucks
    for d, coefs in load.items():  # trucks of the day <= peak of its week
        rows.append(({**coefs, n_x + weeks.index(monday(pd.Timestamp(d).date())): -1.0}, -np.inf, 0.0))
    for w in range(len(weeks)):  # peak of a week <= peak of the period
        rows.append(({n_x + w: 1.0, n - 1: -1.0}, -np.inf, 0.0))
    costs = np.r_[[plan.cost for _, plan in variables], np.zeros(n - n_x)]

    def cheapest(level: float | None):
        extra = [] if level is None else [({c: 1.0 for c in range(n_x, n)}, -np.inf, level + 1e-6)]
        all_rows = rows + extra
        matrix = lil_matrix((len(all_rows), n))
        for r, (coefs, _, _) in enumerate(all_rows):
            for c, v in coefs.items():
                matrix[r, c] = v
        result = milp(costs, constraints=LinearConstraint(matrix.tocsr(), [r[1] for r in all_rows], [r[2] for r in all_rows]),
                      integrality=np.r_[np.ones(n_x), np.zeros(n - n_x)],
                      bounds=Bounds(np.zeros(n), np.r_[np.ones(n_x), np.full(n - n_x, np.inf)]),
                      options={"time_limit": config.smoothing_time_limit, "mip_rel_gap": 0})
        if result.x is None:
            return None
        if result.status != 0:  # time limit: the solution is valid (every rule holds) but not proven optimal
            logging.getLogger(__name__).warning("smoothing level %s: optimum not proven within %.0f s (%s)",
                                                level, config.smoothing_time_limit, result.message)
        plans = {variables[j][0]: variables[j][1] for j in np.flatnonzero(result.x[:n_x] > 0.5)}
        return smoothing_indicator(plans.values()), sum(p.cost for p in plans.values()), plans

    frontier = []
    point = cheapest(None)
    cheapest_cost = point[1]
    while point is not None and point[1] <= cheapest_cost + budget + 1e-6:
        if not frontier or point[0] < frontier[-1][0]:
            frontier.append(point)
        point = cheapest(point[0] - 1)
    return frontier


def smooth(candidates: dict, config: PlannerConfig) -> dict:
    """RG09: spread the delivery days of the couples of each plant, at the lowest cost (exact).

    A couple only moves between plans with the same overflow as its RG07 choice (RG07 kept).
    1. per plant, exact frontier: cheapest plan for each level of the smoothing indicator;
    2. the budget (`smoothing_budget` x cost of the RG07 choice) is shared between the plants so that the
       sum of the indicators is the lowest, then the cost (exhaustive over the frontiers).
    With a zero budget, the planner still takes, among the cheapest plans, the best spread one.
    Disabled (`smoothing=False`), the RG07 choice is kept as is.
    """
    chosen = choose_profiles(candidates, config)
    if not config.smoothing:
        return chosen
    options = smoothing_options(candidates, chosen, config)
    budget = config.smoothing_budget * sum(p.cost for p in chosen.values())
    frontiers = [plant_frontier({k: v for k, v in options.items() if k[1] == plant}, budget, config)
                 for plant in sorted({k[1] for k in options})]
    combos = [(0, 0.0, {})]  # (indicator, cost, plans), Pareto front over the plants seen so far
    for frontier in frontiers:
        merged = [(i + fi, c + fc, {**plans, **fplans}) for i, c, plans in combos for fi, fc, fplans in frontier]
        merged.sort(key=lambda x: (x[1], x[0]))
        combos, best_indicator = [], np.inf
        for point in merged:
            if point[0] < best_indicator:
                combos.append(point)
                best_indicator = point[0]
    limit = sum(p.cost for p in chosen.values()) + budget + 1e-6
    within = [c for c in combos if c[1] <= limit]
    return min(within, key=lambda c: (c[0], c[1]))[2]


# --------------------------------------------------------------------------- reference
def load_baseline(path: Path, lines: pd.DataFrame) -> pd.DataFrame:
    """Reference A computed in Milestone 2 (baseline_deliveries_<imputation>.csv): one row per current delivery.

    Milestone 2 rule (RG12, RG11): one delivery per couple and need day; trucks = ceil(max(weight / 24.5 t,
    volume / 110.4 m3)); full trucks first, the rest in the last truck, each truck priced on its own weight.
    """
    baseline = pd.read_csv(path, parse_dates=["date"])
    required = {"supplier_id", "plant", "date", "weight", "volume", "distance", "trucks", "fill", "cost"}
    if missing := required.difference(baseline.columns):
        raise ValueError(f"Milestone 2 baseline misses columns: {sorted(missing)}")
    same_couples = set(map(tuple, baseline[COUPLE].drop_duplicates().to_numpy())) == set(map(tuple, lines[COUPLE].drop_duplicates().to_numpy()))
    if not same_couples or not np.isclose(baseline["weight"].sum(), lines["weight"].sum()):
        raise ValueError("Milestone 2 baseline does not come from the same consolidated dataset")
    return baseline


def current_situation(lines: pd.DataFrame, baseline: pd.DataFrame, tariff: Tariff,
                      config: PlannerConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reference A loaded truck by truck, like the plan (same reference as the `main` branch).

    The current deliveries are those of Milestone 2 (one per couple and need day, RG12), but their lines are
    loaded whole with `pack` and each truck is priced on its own weight, exactly like the optimised plan:
    both sides of the saving are measured the same way. (The Milestone 2 estimator splits the load
    continuously and rounds the number of trucks up, which counts 3 trucks too many when a delivery is
    exactly 3 full trucks, because of floating-point sums.)

    Returns the deliveries (Milestone 2 columns, `trucks`, `fill` and `cost` recomputed, the Milestone 2
    figures kept in `estimator_trucks` and `estimator_cost`) and one row per truck.
    """
    rows, trucks = [], []
    for (supplier, plant, day), group in lines.groupby(["supplier_id", "plant", "date"], sort=True):
        weight, volume = group["weight"].to_numpy(float), group["volume"].to_numpy(float)
        packed = pack(weight, volume, config)
        kg = np.array([weight[t].sum() for t in packed])
        m3 = np.array([volume[t].sum() for t in packed])
        distance = float(group["distance"].iloc[0])
        prices = tariff(np.full(len(packed), distance), kg)
        rows.append({"supplier_id": supplier, "plant": plant, "date": day, "trucks": len(packed), "cost": float(prices.sum())})
        trucks += [{"supplier_id": supplier, "plant": plant, "date": day, "distance": distance, "weight": w, "volume": v,
                    "fill": max(w / config.truck_kg, v / config.truck_m3), "cost": float(c)}
                   for w, v, c in zip(kg, m3, prices)]
    loaded = pd.DataFrame(rows)
    out = baseline.rename(columns={"trucks": "estimator_trucks", "cost": "estimator_cost"}).merge(
        loaded, on=["supplier_id", "plant", "date"], how="outer", validate="1:1", indicator=True)
    if not (out.pop("_merge") == "both").all():
        raise ValueError("the Milestone 2 deliveries and the dataset lines do not match")
    return out, pd.DataFrame(trucks)


def reference_plan(lines: pd.DataFrame, holidays: set[date], tariff: Tariff, config: PlannerConfig) -> dict:
    """Scenario A rebuilt with the planner's packing (used to compare with `prime`, whose reference packs whole lines)."""
    plans = {}
    for key, group in lines.groupby(COUPLE, sort=True):
        plan = plan_couple(group, tuple(range(5)), holidays, tariff, config, optimize=False)
        if plan is None or (plan.dates != group["date"].to_numpy(dtype="datetime64[D]")).any():
            raise ValueError(f"need outside a working day for {key}")
        plans[key] = plan
    return plans


# --------------------------------------------------------------------------- outputs
def delivery_rule(slot: date | None, delivery: pd.Timestamp) -> str:
    """Why a need leaves on that day (EF10)."""
    if slot is None:
        return "démarrage"
    return "jour du profil" if delivery.date() == slot else "férié avancé"


def export(lines: pd.DataFrame, plans: dict, scenario: str, tariff: Tariff, config: PlannerConfig):
    """Trucks (output 2, header) and allocations (output 2, one row per need)."""
    truck_rows, alloc_rows = [], []
    groups = dict(tuple(lines.groupby(COUPLE, sort=True)))
    for key, plan in sorted(plans.items()):
        group = groups[key]
        distance = float(group["distance"].iloc[0])
        for (day, positions), kg in zip(plan.trucks, plan.truck_weights):
            truck_id = f"{scenario}-{len(truck_rows) + 1:05d}"
            sub = group.iloc[positions]
            m3 = float(sub["volume"].sum())
            day = pd.Timestamp(day)
            truck_rows.append({
                "truck_id": truck_id, "date": day, "weekday": DAY_NAMES[day.weekday()],
                "supplier_id": key[0], "plant": key[1], "distance": distance,
                "lines": len(sub), "weight": float(kg), "volume": m3,
                "fill_weight": kg / config.truck_kg, "fill_volume": m3 / config.truck_m3,
                "fill": max(kg / config.truck_kg, m3 / config.truck_m3),
                "imputed_full_truck": bool(sub["imputed_full_truck"].any()),
                "cost": float(tariff(distance, kg)),
            })
            for position, row in zip(positions, sub.itertuples(index=False)):
                slot = plan.slots[position]
                alloc_rows.append({
                    "truck_id": truck_id, "delivery_date": day, "need_date": row.date,
                    "advance_days": (row.date - day).days, "profile_day": pd.Timestamp(slot) if slot else pd.NaT,
                    "delivery_rule": delivery_rule(slot, day),
                    "line_id": row.line_id, "supplier_id": key[0], "plant": key[1],
                    "unloading_point": row.unloading_point, "material": row.material,
                    "schedule_quantity": row.schedule_quantity, "weight": row.weight, "volume": row.volume,
                    "imputed_full_truck": row.imputed_full_truck,
                })
    return pd.DataFrame(truck_rows), pd.DataFrame(alloc_rows)


def truck_contents(allocations: pd.DataFrame) -> pd.DataFrame:
    """Output 2 in the format of the project brief: per truck, material and dock, the regrouped need days."""
    grouped = allocations.groupby(["truck_id", "delivery_date", "supplier_id", "plant", "unloading_point", "material"])
    return grouped.agg(
        need_days=("need_date", lambda s: ", ".join(sorted({d.strftime("%d/%m") for d in s}))),
        need_lines=("line_id", "size"),
        quantity=("schedule_quantity", "sum"),
        weight=("weight", "sum"),
        volume=("volume", "sum"),
    ).reset_index()


def horizon_weeks(lines: pd.DataFrame) -> list[date]:
    first = monday(lines["date"].min().date())
    return [first + timedelta(weeks=w) for w in range((monday(lines["date"].max().date()) - first).days // 7 + 1)]


def couple_summary(lines: pd.DataFrame, baseline: pd.DataFrame, plans_B: dict, candidates: dict, unsmoothed: dict,
                   config: PlannerConfig) -> pd.DataFrame:
    """Outputs 1 and 3: profile, weekly pattern and saving per couple, with the reason of each choice (EF10).

    `baseline` = reference A of Milestone 2, one row per current delivery.
    """
    weeks = horizon_weeks(lines)
    imputed = lines.groupby(COUPLE)["imputed_full_truck"].sum()
    current = dict(tuple(baseline.groupby(COUPLE, sort=True)))
    days = lambda profile: " + ".join(DAY_NAMES[d] for d in profile)
    rows = []
    for key, plan in sorted(plans_B.items()):
        before, rg07 = current[key], unsmoothed[key]
        before_weeks = pd.Series([monday(d.date()) for d in before["date"]]).value_counts()
        pattern_before = [int(before_weeks.get(w, 0)) for w in weeks]
        pattern_after = [plan.weekly_deliveries.get(w, 0) for w in weeks]
        lower = [p for p in candidates[key] if p.frequency < rg07.frequency]
        if rg07.overflow_departures:
            why = f"profil {rg07.frequency} : des départs dépassent un camion quel que soit le plan, on ajoute des camions"
        else:
            why = f"profil {rg07.frequency} : meilleur plan dont chaque départ tient dans un camion"
            fitting_lower = [p for p in lower if p.overflow_departures == 0]
            if fitting_lower:
                alt = min(fitting_lower, key=lambda p: selection_key(p, config))
                why += f" (profil {alt.frequency} possible : {alt.n_trucks - rg07.n_trucks:+d} camion(s), {alt.cost - rg07.cost:+.0f} EUR)"
            elif lower:
                alt = min(lower, key=lambda p: p.overflow_departures)
                why += (f" ; avec moins de jours, {alt.overflow_departures} départ(s) dépasseraient un camion "
                        f"(le client préfère ajouter des jours plutôt que des camions)")
        if plan.frequency != rg07.frequency:
            why += f" ; lissage : profil {plan.frequency}"
        if plan.profile != rg07.profile:
            why += f" ; lissage : {days(plan.profile)} plutôt que {days(rg07.profile)} ({plan.cost - rg07.cost:+.0f} EUR)"
        rows.append({
            "supplier_id": key[0], "plant": key[1], "distance": float(lines.loc[lines["supplier_id"] == key[0], "distance"].iloc[0]),
            "current_profile": max(pattern_before), "current_weekly_pattern": " ".join(map(str, pattern_before)),
            "profile": plan.frequency, "profile_days": days(plan.profile),
            "weekly_pattern": " ".join(map(str, pattern_after)),
            "startup_deliveries": len({d for s, d in zip(plan.slots, plan.dates.tolist()) if s is None}),
            "deliveries_before": len(before), "deliveries_after": len(np.unique(plan.dates)),
            "trucks_before": int(before["trucks"].sum()), "trucks_after": plan.n_trucks,
            "trucks_per_active_week": plan.n_trucks / max(1, sum(n > 0 for n in pattern_after)),
            "mean_fill_weight_pct": 100 * float(np.mean(plan.truck_weights)) / config.truck_kg,
            "mean_advance_days": plan.total_advance / len(plan.slots),
            "overflow_departures": plan.overflow_departures, "imputed_full_truck_lines": int(imputed[key]),
            "cost_before": float(before["cost"].sum()), "cost_after": plan.cost,
            "saving": float(before["cost"].sum()) - plan.cost, "reason": why,
        })
    out = pd.DataFrame(rows)
    out["saving_pct"] = 100 * out["saving"] / out["cost_before"]
    return out.sort_values("saving", ascending=False).reset_index(drop=True)


def plant_day_load(table: pd.DataFrame) -> pd.DataFrame:
    """Trucks received per plant and per day (RG09 indicator).

    `table` = one row per truck, or one row per delivery with a `trucks` column (Milestone 2 baseline).
    """
    grouped = table.groupby(["plant", "date"])
    counts = grouped["trucks"].sum() if "trucks" in table.columns else grouped.size()
    return counts.unstack("plant", fill_value=0)


# --------------------------------------------------------------------------- automatic checks (EF09, Milestone 4)
def first_profile_day(profile: tuple[int, ...], start: pd.Timestamp, holidays: set[date]) -> pd.Timestamp:
    """First delivery day of a profile on or after the start of the plan (RG10 shift included)."""
    nodes = departure_nodes([start.date() + timedelta(days=13)], profile, holidays, start.date())
    return pd.Timestamp(min(d for s, d in nodes if s is not None))


def rg06_coverage(allocations: pd.DataFrame) -> bool:
    """RG06: the departure of a need is the latest departure of its couple on or before the need date."""
    for _, group in allocations.groupby(COUPLE):
        departures = np.sort(group["delivery_date"].unique())
        latest = departures[np.searchsorted(departures, group["need_date"].to_numpy(), side="right") - 1]
        if (latest != group["delivery_date"].to_numpy()).any():
            return False
    return True


def check(lines: pd.DataFrame, plans: dict, trucks: pd.DataFrame, allocations: pd.DataFrame,
          holidays: set[date], tariff: Tariff, config: PlannerConfig, candidates: dict | None = None) -> pd.DataFrame:
    """Verify every business rule on a finished plan, independently of how it was built.

    One row per check: rule, what is verified, True = respected. RG07 is checked only when the
    candidate plans are given.
    """
    start = pd.Timestamp(first_working_day(plan_start(lines, config), holidays))
    alloc = allocations.copy()
    per_truck = allocations.groupby("truck_id").agg(kg=("weight", "sum"), m3=("volume", "sum"), lines=("line_id", "size"))
    t = trucks.set_index("truck_id")
    demand = lines.groupby(["plant", "material"])["schedule_quantity"].sum()
    delivered = allocations.groupby(["plant", "material"])["schedule_quantity"].sum()
    served = alloc.dropna(subset=["profile_day"])  # needs delivered on a profile day (start-up excluded)
    days_used = served.groupby(COUPLE)["profile_day"].apply(lambda s: set(s.dt.day_of_week))
    max_per_week = (served.drop_duplicates([*COUPLE, "profile_day"])
                    .assign(week=lambda d: d["profile_day"] - pd.to_timedelta(d["profile_day"].dt.day_of_week, unit="D"))
                    .groupby([*COUPLE, "week"]).size().groupby(level=[0, 1]).max())
    shifted = served[served["delivery_rule"].eq("férié avancé")]
    rows = [
        ("RG01", "Toutes les lignes d'un camion ont le même fournisseur et la même usine",
         allocations.groupby("truck_id")[COUPLE].nunique().max().max() == 1),
        ("RG02", "Aucune ligne n'est livrée après sa date de besoin (avance ≥ 0)",
         (alloc["advance_days"] >= 0).all()),
        ("RG03", f"Aucune ligne n'est livrée plus de {config.max_advance_days} jours calendaires avant sa date",
         (alloc["advance_days"] <= config.max_advance_days).all()),
        ("RG04", f"Somme des poids ≤ {f'{config.truck_kg:,.0f}'.replace(',', ' ')} kg et somme des volumes ≤ "
                 f"{str(config.truck_m3).replace('.', ',')} m³, camion par camion, recalculées à partir des lignes",
         (per_truck["kg"] <= config.truck_kg + 1e-6).all() and (per_truck["m3"] <= config.truck_m3 + 1e-6).all()),
        ("Profil - mêmes jours", "Chaque couple livre toujours sur le même ensemble de jours, et le nombre de jours de "
                                 "cet ensemble = le maximum de livraisons faites en une semaine (= profil affiché)",
         all(len(days_used[k]) == max_per_week[k] == plans[k].frequency and days_used[k] == set(plans[k].profile)
             for k in days_used.index)),
        ("RG05 - profil", "Le profil de chaque couple fait partie des profils autorisés",
         all(p.frequency in config.allowed_frequencies for p in plans.values())),
        ("RG05 - jours", "Chaque livraison tombe un jour du profil du couple, sauf démarrage (première semaine) "
                         "ou jour férié avancé",
         (served.loc[served["delivery_rule"].eq("jour du profil"), "delivery_date"]
          == served.loc[served["delivery_rule"].eq("jour du profil"), "profile_day"]).all()
         and all(d.weekday() in plans[(s, p)].profile
                 for d, s, p in served[["profile_day", "supplier_id", "plant"]].itertuples(index=False))
         and (alloc.loc[alloc["delivery_rule"].eq("démarrage"), "delivery_date"] < start + pd.Timedelta(days=7)).all()),
        ("RG06", "Chaque besoin part avec le dernier départ de son couple à sa date ou avant : un jour de "
                 "livraison couvre les besoins jusqu'au jour de livraison suivant, exclu",
         rg06_coverage(allocations))
        if config.strict_coverage else
        ("RG06 (assouplie)", f"Chaque besoin part avec un départ prévu de son couple compris dans sa fenêtre "
                             f"[date − {config.max_advance_days} j ; date] ; un départ de démarrage ne porte que des "
                             f"besoins datés avant le premier jour du profil",
         bool(((alloc["need_date"] - alloc["delivery_date"]).dt.days.between(0, config.max_advance_days)).all())),
    ]
    if candidates is not None:
        if config.lowest_profile_first:
            rows.append(("RG07", "Aucun profil plus bas n'aurait évité les débordements aussi bien",
                         all(not any(p.frequency < plans[k].frequency and p.overflow_departures <= plans[k].overflow_departures
                                     for p in candidates[k]) for k in plans)))
        elif config.overflow_first:
            rows.append(("RG07", "Aucun autre plan du couple n'a moins de départs dépassant un camion "
                                 "(le client préfère ajouter des jours plutôt que des camions)",
                         all(plans[k].overflow_departures == min(p.overflow_departures for p in candidates[k])
                             for k in plans)))
        else:
            cheapest = sum(min(p.cost for p in candidates[k]) for k in plans)
            budget = config.smoothing_budget if config.smoothing else 0.0
            rows.append(("RG07 - coût", "Le coût total ne dépasse pas la somme des plans les moins chers de chaque "
                                        "couple de plus que le budget de lissage",
                         sum(p.cost for p in plans.values()) <= cheapest * (1 + budget) + 1e-6))
    rows += [
        ("RG08 - unicité", "Chaque ligne du dataset est livrée une et une seule fois",
         alloc["line_id"].is_unique and set(alloc["line_id"]) == set(lines["line_id"])),
        ("RG08 - quantités", "Quantité livrée = quantité demandée, pour chaque usine et chaque material",
         np.allclose(delivered.reindex(demand.index, fill_value=0), demand)),
        ("RG10", "Aucune livraison un jour férié ou un week-end ; un jour de profil férié est livré le jour ouvré précédent",
         all(is_working_day(d.date(), holidays) for d in trucks["date"])
         and all(pd.Timestamp(s).date() in holidays and d < s for s, d in shifted[["profile_day", "delivery_date"]].itertuples(index=False))),
        ("Démarrage", "Aucune livraison avant le premier jour du plan ; un départ de démarrage ne porte que des besoins "
                      "datés avant le premier jour du profil (aucun jour de profil dans leur fenêtre)",
         (trucks["date"] >= start).all()
         and all(row.need_date < first_profile_day(plans[(row.supplier_id, row.plant)].profile, start, holidays)
                 for row in alloc[alloc["delivery_rule"].eq("démarrage")].itertuples(index=False))),
        ("RG11 - totaux", "Poids, volume et nombre de lignes de chaque camion = somme de ses lignes",
         np.allclose(t.loc[per_truck.index, "weight"], per_truck["kg"])
         and np.allclose(t.loc[per_truck.index, "volume"], per_truck["m3"])
         and (t.loc[per_truck.index, "lines"] == per_truck["lines"]).all()),
        ("RG11 - coût", "Coût de chaque camion = grille(distance, poids) × (1 + marge) × diesel",
         np.allclose(trucks["cost"], tariff(trucks["distance"], trucks["weight"]))),
    ]
    return pd.DataFrame([(name, what, bool(ok)) for name, what, ok in rows],
                        columns=["contrôle", "ce qui est vérifié", "respecté"])
