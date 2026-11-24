"""Delivery options of a need, evaluation of an assignment and greedy plan of a couple."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..domain.calendar import first_working_day, is_working_day, monday
from ..domain.config import PlannerConfig
from ..domain.models import CouplePlan, Option, SortKey
from ..pricing.tariff import Tariff
from .loading import pack


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
    options: list[Option] = []
    seen: set[date] = set()
    for advance in range(max_advance + 1):
        slot = day - timedelta(days=advance)
        if slot.weekday() not in days:
            continue
        delivery: date | None = slot
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


def evaluate(lines: pd.DataFrame, options: list[list[Option]], choice: list[int], allowed_days: tuple[int, ...],
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


def plan_key(plan: CouplePlan) -> SortKey:
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
