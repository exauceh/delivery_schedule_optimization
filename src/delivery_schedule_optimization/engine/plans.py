"""Delivery options of a need, evaluation of an assignment and latest-day plan of a couple."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..domain.calendar import is_working_day, monday
from ..domain.config import PlannerConfig
from ..domain.models import CouplePlan, Option
from ..pricing.tariff import Tariff
from .loading import pack


def delivery_options(day: date, days: tuple[int, ...], holidays: set[date], max_advance: int) -> list[Option]:
    """Possible (profile day, delivery date) pairs for a need dated `day`, latest first.

    - a profile day = a day of the set within [day - max_advance, day] (RG03, RG05);
    - RG10: a profile day that is a public holiday is delivered on the previous working day, still
      within the window.
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
        if delivery is None or delivery in seen:
            continue
        seen.add(delivery)
        options.append((slot, delivery))
    return options


def delivery_dates(due: np.ndarray, days: tuple[int, ...], holidays: set[date], max_advance: int) -> np.ndarray | None:
    """Latest possible delivery date of every need (RG06 starting point), or None if a need is uncovered."""
    out = []
    for d in due:
        options = delivery_options(pd.Timestamp(d).date(), days, holidays, max_advance)
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
    used = sorted(set(slots))
    weekly: dict[date, int] = defaultdict(int)
    for slot in used:
        weekly[monday(slot)] += 1
    return CouplePlan(
        allowed_days=allowed_days, profile=tuple(sorted({s.weekday() for s in used})), slots=slots, dates=dates,
        trucks=trucks, truck_weights=truck_weights, cost=cost, overflow_departures=int(overflow),
        total_advance=int((due - dates).astype(int).sum()), weekly_deliveries=dict(weekly),
    )


def evaluate_departures(lines: pd.DataFrame, departures: list[Option], allowed_days: tuple[int, ...],
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


def plan_couple(lines: pd.DataFrame, days: tuple[int, ...], holidays: set[date], tariff: Tariff,
                config: PlannerConfig) -> CouplePlan | None:
    """Plan one couple on one set of days, every need on its latest possible day (RG06).

    Returns None if a need cannot be covered.
    """
    by_date = {d: delivery_options(d, days, holidays, config.max_advance_days) for d in lines["date"].dt.date.unique()}
    if any(not opts for opts in by_date.values()):
        return None
    options = [by_date[d] for d in lines["date"].dt.date]
    return evaluate(lines, options, [0] * len(options), days, tariff, config)
