"""Exact plan of one couple on one set of days: shortest path over the departure days (dynamic programming)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import cast

import numpy as np
import pandas as pd

from ..domain.calendar import first_working_day, is_working_day, monday
from ..domain.config import PlannerConfig
from ..domain.models import CouplePlan, Option
from ..pricing.tariff import Tariff
from .loading import pack
from .plans import evaluate_departures

Criteria = tuple[float, ...]  # (overflow, price, trucks, advance) or (overflow, trucks, price, advance)
State = tuple[object, ...]  # () or (weekdays mask, busiest week reached, week, departures in the week)
Label = tuple[Criteria, tuple[int, State] | None]  # best criteria to reach a node, and its predecessor


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
                 config: PlannerConfig, start: date, startup_need_day: bool = False,
                 same_days: bool = True) -> CouplePlan | None:
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
    With `same_days=False` the "same days" rule is ignored (lower bound of the cost, for information only).
    """
    first_day = first_working_day(start, holidays)
    due_dates = lines["date"].dt.date.to_numpy()
    nodes = departure_nodes(list(due_dates), days, holidays, first_day)
    if not nodes or nodes[0][1] > min(due_dates):
        return None
    weight, volume = lines["weight"].to_numpy(float), lines["volume"].to_numpy(float)
    imputed = lines["imputed_full_truck"].to_numpy(bool)
    distance = float(lines["distance"].iloc[0])
    cache: dict[tuple[int, int | None], Criteria | str | None] = {}
    startup_days = {d for s, d in nodes if s is None}
    first_profile = min((d for s, d in nodes if s is not None), default=date.max)
    EMPTY = "empty"  # a departure must carry at least one need

    def segment(i: int, j: int | None) -> Criteria | str | None:
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

    def add(a: Criteria, b: Criteria) -> Criteria:
        return tuple(x + y for x, y in zip(a, b))

    def solve(track: bool) -> list[Option] | None:
        """Shortest path; with `track`, the state also holds (days used, busiest week reached, week, count)."""
        full_mask = sum(1 << d for d in days)

        def step(state: State, node: Option) -> State:
            slot = node[0]
            if not track or slot is None:
                return state
            mask, full, week, count = cast(tuple[int, bool, date | None, int], state)
            count = count + 1 if monday(slot) == week else 1
            return (mask | 1 << slot.weekday(), full or count == len(days), monday(slot), count)

        empty: State = (0, len(days) == 1, None, 0) if track else ()
        best: list[dict[State, Label]] = [dict() for _ in nodes]
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
                if isinstance(cost, str):  # EMPTY
                    continue
                for state, (key, _) in best[i].items():
                    new_state, new_key = step(state, node), add(key, cost)
                    if new_state not in best[j] or new_key < best[j][new_state][0]:
                        best[j][new_state] = (new_key, (i, state))
        final: tuple[Criteria, int, State] | None = None
        for i in range(len(nodes)):
            cost = segment(i, None) if best[i] else None
            if cost is None or isinstance(cost, str):  # forbidden or EMPTY
                continue
            for state, (key, _) in best[i].items():
                if track and (state[0] != full_mask or not state[1]):
                    continue
                if final is None or add(key, cost) < final[0]:
                    final = (add(key, cost), i, state)
        if final is None:
            return None
        _, last, state = final
        path: list[Option] = []
        position: int | None = last
        while position is not None:
            path.append(nodes[position])
            link = best[position][state][1]
            position, state = link if link is not None else (None, state)
        return path

    for track in (False, True):
        path = solve(track)
        if path is None:
            return None
        plan = evaluate_departures(lines, path, days, tariff, config)
        if not same_days or (plan.same_days and plan.frequency in config.allowed_frequencies):
            return plan
    return None
