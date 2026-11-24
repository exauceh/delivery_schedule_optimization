"""RG06 relaxed: local search that improves the assignment of the needs to the planned departures."""

from __future__ import annotations

import itertools
import logging
from collections import defaultdict
from datetime import date

import numpy as np
import pandas as pd

from ..domain.calendar import first_working_day
from ..domain.config import PlannerConfig
from ..domain.models import CouplePlan
from ..pricing.tariff import Tariff
from .graph import departure_nodes
from .loading import pack
from .plans import evaluate


def reassign(lines: pd.DataFrame, plan: CouplePlan, holidays: set[date], tariff: Tariff, config: PlannerConfig,
             start: date, same_days: bool = True) -> CouplePlan:
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

    def valid(new_assign: list[date]) -> CouplePlan | None:
        candidate = evaluate(lines, [[(slot_of[d], d)] for d in new_assign], [0] * len(new_assign),
                             plan.allowed_days, tariff, config)
        ok = not same_days or (candidate.same_days and candidate.frequency in config.allowed_frequencies)
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
                    members[src].discard(i)
                    members[dst].add(i)
                    current[src], current[dst] = after_src, after_dst
                    assign, improved, src = new_assign, True, dst
        if not improved:
            improved = move_pair()  # pairs only once single moves are exhausted
        if not improved:
            break
    result = valid(assign)
    if result is None:
        return plan
    better = (
        (result.n_trucks, result.cost) < (plan.n_trucks, plan.cost - 1e-9) if config.objective == "trucks"
        else result.cost < plan.cost - 1e-9)
    return result if better else plan
