"""Smoothing of the delivery days per plant (RG09): integer programme on the candidate plans."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import date

import numpy as np
import pandas as pd

from ..domain.calendar import monday
from ..domain.config import PlannerConfig
from ..domain.models import Candidates, CouplePlan, Plans
from .selection import choose_profiles, selection_key

FrontierPoint = tuple[int, float, Plans]  # (smoothing indicator, cost, one plan per couple)


def smoothing_options(candidates: Candidates, chosen: Plans, config: PlannerConfig = PlannerConfig()) -> Candidates:
    """Plans a couple may take when smoothing: same number of overflowing departures as its RG07 choice
    (and the same profile with `lowest_profile_first`), so that RG07 stays respected."""
    return {
        key: sorted([p for p in plans if (not config.overflow_first or p.overflow_departures == chosen[key].overflow_departures)
                     and (not config.lowest_profile_first or p.frequency == chosen[key].frequency)],
                    key=lambda p: selection_key(p, config))
        for key, plans in candidates.items()
    }


def smoothing_indicator(plans: Iterable[CouplePlan]) -> int:
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


def plant_frontier(options: Candidates, budget: float, config: PlannerConfig) -> list[FrontierPoint]:
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

    def cheapest(level: float | None) -> FrontierPoint | None:
        extra: list[tuple[dict[int, float], float, float]] = [] if level is None else [({c: 1.0 for c in range(n_x, n)}, -np.inf, level + 1e-6)]
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

    frontier: list[FrontierPoint] = []
    point = cheapest(None)
    if point is None:
        raise ValueError("smoothing: no feasible choice of one plan per couple for this plant")
    cheapest_cost = point[1]
    while point is not None and point[1] <= cheapest_cost + budget + 1e-6:
        if not frontier or point[0] < frontier[-1][0]:
            frontier.append(point)
        point = cheapest(point[0] - 1)
    return frontier


def smooth(candidates: Candidates, config: PlannerConfig) -> Plans:
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
    combos: list[FrontierPoint] = [(0, 0.0, {})]  # (indicator, cost, plans), Pareto front over the plants seen so far
    for frontier in frontiers:
        merged = [(i + fi, c + fc, {**plans, **fplans}) for i, c, plans in combos for fi, fc, fplans in frontier]
        merged.sort(key=lambda x: (x[1], x[0]))
        combos = []
        best_indicator: float = np.inf
        for point in merged:
            if point[0] < best_indicator:
                combos.append(point)
                best_indicator = point[0]
    limit = sum(p.cost for p in chosen.values()) + budget + 1e-6
    within = [c for c in combos if c[1] <= limit]
    return min(within, key=lambda c: (c[0], c[1]))[2]
