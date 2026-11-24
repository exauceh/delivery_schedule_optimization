"""Final exact re-optimisation of each couple, line by line (OR-Tools CP-SAT).

The weekdays of the profile and the delivery days stay those of the smoothed plan; on each delivery day the
number of trucks may not increase. So the daily peak of every plant and the 'profile reached' rule are kept,
and only the cost can go down. Each line may go to any delivery day of its window, and trucks are built
exactly (each truck priced on its own weight): this removes the 'whole need day' assumption of the dynamic
programming and the approximation of the fast packing.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from ortools.sat.python import cp_model

from .config import Config
from .data import Calendar, RateSheet


def polish_couple(cfg: Config, rates: RateSheet, cal: Calendar, row: int, lines: list, plan: dict, S: tuple):
    """lines: (need_day, weight, volume, line_id); plan: {line_id: delivery_day}.
    Returns {line_id: (day, truck_no)} and its cost, or None if no better plan is proven."""
    H = dt.timedelta(days=cfg.anticipation_days)
    info = {i: (w, v, t) for t, w, v, i in lines}
    days = sorted(set(plan.values()))
    # current trucks per day (fast packing) = cap per day
    from .loading import Loader
    loader = Loader(cfg, rates)
    by = defaultdict(list)
    for i, d in plan.items():
        by[d].append((info[i][0], info[i][1], i))
    cap = {d: min(loader.cost(by[d], row)[1], len(lines)) for d in days}
    current = sum(loader.cost(by[d], row)[0] for d in set(plan.values()))
    cand = {i: [d for d in days if info[i][2] - H <= d <= info[i][2]] for i in info}
    if all(len(c) == 1 for c in cand.values()) and all(cap[d] == 1 for d in days):
        return None                                          # nothing can move and no truck to rebuild
    # reference week of rule 24 in the current plan: keep a delivery on each of its days
    ref = next(([d for d in days if Calendar.week_key(d) == w and d.weekday() in S] for w in cal.full_weeks
                if {d.weekday() for d in days if Calendar.week_key(d) == w} >= set(S)), [])
    price = [round(p * 100) for p in rates.grid[row] * rates.factor]
    ub = [int(round(min(b, cfg.truck_kg) * 100)) for b in rates.kg_bounds]
    W = {i: int(round(info[i][0] * 100)) for i in info}
    V = {i: int(round(info[i][1] * 1000)) for i in info}
    capV = int(round(cfg.truck_m3 * 1000))
    m = cp_model.CpModel()
    x, used, y = {}, {}, {}
    for d in days:
        for j in range(cap[d]):
            used[d, j] = m.NewBoolVar("")
            if j:
                m.Add(used[d, j] <= used[d, j - 1])
            for k in range(len(price)):
                y[d, j, k] = m.NewBoolVar("")
            m.Add(sum(y[d, j, k] for k in range(len(price))) == used[d, j])
    for i, c in cand.items():
        for d in c:
            for j in range(cap[d]):
                x[i, d, j] = m.NewBoolVar("")
                m.AddImplication(x[i, d, j], used[d, j])
        m.AddExactlyOne(x[i, d, j] for d in c for j in range(cap[d]))
    for d in days:
        for j in range(cap[d]):
            members = [i for i in cand if d in cand[i]]
            m.Add(sum(W[i] * x[i, d, j] for i in members) <= sum(ub[k] * y[d, j, k] for k in range(len(price))))
            m.Add(sum(V[i] * x[i, d, j] for i in members) <= capV * used[d, j])
            m.Add(sum(x[i, d, j] for i in members) >= used[d, j])               # no empty truck
    for d in ref:
        m.Add(used[d, 0] == 1)
    # start from the current plan
    for d in set(plan.values()):
        for j, b in enumerate(loader.ffd(by[d], row)):
            for i in b[2]:
                m.AddHint(x[i, d, j], True)
    m.Minimize(sum(price[k] * y[d, j, k] for d in days for j in range(cap[d]) for k in range(len(price))))
    s = cp_model.CpSolver()
    s.parameters.num_workers = 1
    s.parameters.random_seed = cfg.solver_seed
    s.parameters.max_deterministic_time = cfg.polish_deterministic_time
    if s.Solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    out = {i: (d, j) for (i, d, j), v in x.items() if s.Value(v)}
    trucks = defaultdict(float)
    for i, (d, j) in out.items():
        trucks[d, j] += info[i][0]
    cost = sum(rates.price(row, w) for w in trucks.values())
    return (out, cost) if cost < current - 0.005 else None
