"""Delivery consolidation engine (milestone 3).

One MILP per couple supplier x plant and per profile N (at most N delivery weekdays per week).
The couples are independent, so every (couple, N) problem is solved in parallel in its own process.
The mathematical model is documented in milestone3/milestone3.ipynb.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt
import pulp

logger = logging.getLogger(__name__)

N_WEEKDAYS = 5  # Monday .. Friday


@dataclass(frozen=True)
class SolverSettings:
    """Parameters shared by every MILP solve."""

    truck_kg: float
    truck_m3: float
    max_anticipation_days: int
    delivery_penalty_eur: float
    time_limit_s: float
    mip_gap: float


@dataclass(frozen=True)
class CoupleInstance:
    """Data of one couple: its lines of need and the price of one truck in each weight bracket."""

    supplier_id: str
    plant: str
    distance: float
    line_ids: npt.NDArray[np.int64]
    need_days: npt.NDArray[np.datetime64]  # day of need of each line (datetime64[D])
    weights: npt.NDArray[np.float64]
    volumes: npt.NDArray[np.float64]
    prices: npt.NDArray[np.float64]  # EUR for one truck in each weight bracket
    bracket_upper: npt.NDArray[np.float64]  # upper weight bound of each bracket (last one = truck capacity)

    @property
    def key(self) -> tuple[str, str]:
        return self.supplier_id, self.plant


@dataclass(frozen=True)
class CoupleSolution:
    """Plan of one couple for one profile N."""

    n_max: int
    model_cost: float  # EUR, transport cost computed by the model (without the epsilon)
    status: str  # solution status reported by the solver
    seconds: float  # solve time; 0 when the plan was taken from another profile
    delivery_days: npt.NDArray[np.datetime64]  # delivery day of each line, same order as line_ids
    weekdays: tuple[int, ...]  # weekdays used by the plan (0 = Monday)


def weekday(days: npt.NDArray[np.datetime64]) -> npt.NDArray[np.int64]:
    """Day of the week of datetime64[D] values (0 = Monday); 1970-01-01 was a Thursday."""
    return ((days.astype("datetime64[D]").astype(np.int64) + 3) % 7).astype(np.int64)


def solve_couple(
    inst: CoupleInstance,
    n_max: int,
    calendar: npt.NDArray[np.datetime64],
    settings: SolverSettings,
    window_days: int | None = None,
) -> CoupleSolution:
    """Build and solve the MILP of one couple for the profile n_max.

    calendar: sorted possible delivery days (working days, datetime64[D]).
    window_days: anticipation window; defaults to settings.max_anticipation_days.
    """
    window = settings.max_anticipation_days if window_days is None else window_days
    days = calendar.astype("datetime64[D]")
    need = inst.need_days.astype("datetime64[D]")
    first = np.searchsorted(days, need - np.timedelta64(window, "D"), side="left")
    last = np.searchsorted(days, need, side="right")  # A_l = days[first:last]
    if (first >= last).any():
        raise ValueError(f"{inst.supplier_id} x {inst.plant}: a line has no possible delivery day")
    day_weekday = weekday(days)

    prob = pulp.LpProblem(f"{inst.supplier_id}_{inst.plant}_N{n_max}", pulp.LpMinimize)
    by_line: list[list[tuple[int, pulp.LpVariable]]] = []
    by_day: dict[int, list[tuple[int, pulp.LpVariable]]] = {}
    for li in range(len(need)):
        options = []
        for dj in range(int(first[li]), int(last[li])):
            var = pulp.LpVariable(f"z_{li}_{dj}", cat=pulp.LpBinary)
            options.append((dj, var))
            by_day.setdefault(dj, []).append((li, var))
        by_line.append(options)

    brackets = range(len(inst.bracket_upper))
    x = {dj: pulp.LpVariable(f"x_{dj}", cat=pulp.LpBinary) for dj in by_day}
    full = {dj: pulp.LpVariable(f"F_{dj}", lowBound=0, cat=pulp.LpInteger) for dj in by_day}
    y = {(dj, k): pulp.LpVariable(f"y_{dj}_{k}", cat=pulp.LpBinary) for dj in by_day for k in brackets}
    s = {d: pulp.LpVariable(f"s_{d}", cat=pulp.LpBinary) for d in range(N_WEEKDAYS)}

    transport = pulp.lpSum(float(inst.prices[-1]) * full[dj] for dj in by_day) + pulp.lpSum(
        float(inst.prices[k]) * var for (dj, k), var in y.items()
    )
    prob += transport + settings.delivery_penalty_eur * pulp.lpSum(x.values())

    for options in by_line:  # (1) every line delivered once, inside its window
        prob += pulp.lpSum(var for _, var in options) == 1
    for dj, assigned in by_day.items():
        for _, var in assigned:  # (2) no line on a day without delivery
            prob += var <= x[dj]
        prob += pulp.lpSum(float(inst.weights[li]) * var for li, var in assigned) <= settings.truck_kg * full[
            dj
        ] + pulp.lpSum(float(inst.bracket_upper[k]) * y[dj, k] for k in brackets)  # (3) weight
        prob += pulp.lpSum(float(inst.volumes[li]) * var for li, var in assigned) <= settings.truck_m3 * (
            full[dj] + pulp.lpSum(y[dj, k] for k in brackets)
        )  # (4) volume
        prob += pulp.lpSum(y[dj, k] for k in brackets) == x[dj]  # (5) one last truck per delivery
        prob += full[dj] <= len(assigned) * x[dj]  # (6) no truck without delivery
        prob += x[dj] <= s[int(day_weekday[dj])]  # (7) fixed weekdays
    prob += pulp.lpSum(s.values()) <= n_max  # (7) at most n_max weekdays

    start = time.perf_counter()
    prob.solve(pulp.HiGHS(msg=False, timeLimit=settings.time_limit_s, gapRel=settings.mip_gap, threads=1))
    seconds = time.perf_counter() - start

    chosen = np.full(len(need), -1, dtype=np.int64)
    for li, options in enumerate(by_line):
        for dj, var in options:
            if (var.value() or 0.0) > 0.5:
                chosen[li] = dj
                break
    if (chosen < 0).any():
        raise RuntimeError(
            f"{inst.supplier_id} x {inst.plant} N={n_max}: no feasible plan found ({pulp.LpStatus[prob.status]})"
        )
    delivery = days[chosen]
    return CoupleSolution(
        n_max=n_max,
        model_cost=float(pulp.value(transport)),
        status=pulp.LpSolution[prob.sol_status],
        seconds=seconds,
        delivery_days=delivery,
        weekdays=tuple(sorted({int(d) for d in weekday(delivery)})),
    )


@dataclass(frozen=True)
class DeliveryLoad:
    """Whole lines of one delivery loaded into trucks."""

    truck_of_line: npt.NDArray[np.int64]  # truck number (1, 2, ...) of each line, in the input order
    truck_weights: npt.NDArray[np.float64]
    truck_volumes: npt.NDArray[np.float64]
    truck_costs: npt.NDArray[np.float64]  # EUR, each truck priced on its own weight
    status: str  # "single truck" or the solution status of the loading MILP

    @property
    def n_trucks(self) -> int:
        return len(self.truck_costs)


def truck_cost(weight: float, prices: npt.NDArray[np.float64], bracket_upper: npt.NDArray[np.float64]) -> float:
    """Price of one truck carrying `weight` kg: smallest bracket whose inclusive upper bound covers it."""
    k = int(np.searchsorted(bracket_upper, weight, side="left"))
    if k >= len(bracket_upper):
        raise ValueError(f"{weight:.1f} kg exceeds the capacity of a truck")
    return float(prices[k])


def _pack(
    weights: npt.NDArray[np.float64],
    volumes: npt.NDArray[np.float64],
    prices: npt.NDArray[np.float64],
    bracket_upper: npt.NDArray[np.float64],
    settings: SolverSettings,
    slots: int,
) -> DeliveryLoad | None:
    """Loading MILP with at most `slots` trucks; None when no loading fits in that many trucks."""
    lines, trucks, brackets = range(len(weights)), range(slots), range(len(bracket_upper))
    prob = pulp.LpProblem("loading", pulp.LpMinimize)
    a = {(i, b): pulp.LpVariable(f"a_{i}_{b}", cat=pulp.LpBinary) for i in lines for b in trucks}
    used = {b: pulp.LpVariable(f"t_{b}", cat=pulp.LpBinary) for b in trucks}
    y = {(b, k): pulp.LpVariable(f"y_{b}_{k}", cat=pulp.LpBinary) for b in trucks for k in brackets}
    # A truck costs more than any price difference: first the fewest trucks, then the cheapest brackets
    truck_penalty = slots * float(prices.max()) + 1.0
    prob += truck_penalty * pulp.lpSum(used.values()) + pulp.lpSum(float(prices[k]) * var for (_, k), var in y.items())
    for i in lines:  # a line is never split: exactly one truck
        prob += pulp.lpSum(a[i, b] for b in trucks) == 1
    for b in trucks:
        prob += pulp.lpSum(y[b, k] for k in brackets) == used[b]  # a used truck has one price bracket
        prob += pulp.lpSum(float(weights[i]) * a[i, b] for i in lines) <= pulp.lpSum(
            float(bracket_upper[k]) * y[b, k] for k in brackets
        )  # weight within the bracket (the last bracket is capped by the truck capacity)
        prob += pulp.lpSum(float(volumes[i]) * a[i, b] for i in lines) <= settings.truck_m3 * used[b]
        if b > 0:  # symmetry breaking: trucks are used in order
            prob += used[b] <= used[b - 1]
    prob.solve(pulp.HiGHS(msg=False, timeLimit=settings.time_limit_s, threads=1))
    if prob.status != pulp.LpStatusOptimal:
        return None

    slot_of_line = np.array([next(b for b in trucks if (a[i, b].value() or 0.0) > 0.5) for i in lines])
    loads = sorted(set(slot_of_line.tolist()), key=lambda b: -float(weights[slot_of_line == b].sum()))
    number = {slot: n for n, slot in enumerate(loads, start=1)}  # truck 1 = heaviest
    truck_of_line = np.array([number[int(b)] for b in slot_of_line], dtype=np.int64)
    truck_weights = np.array([weights[truck_of_line == n].sum() for n in range(1, len(loads) + 1)])
    truck_volumes = np.array([volumes[truck_of_line == n].sum() for n in range(1, len(loads) + 1)])
    return DeliveryLoad(
        truck_of_line=truck_of_line,
        truck_weights=truck_weights,
        truck_volumes=truck_volumes,
        truck_costs=np.array([truck_cost(w, prices, bracket_upper) for w in truck_weights]),
        status=pulp.LpSolution[prob.sol_status],
    )


def load_delivery(
    weights: npt.NDArray[np.float64],
    volumes: npt.NDArray[np.float64],
    prices: npt.NDArray[np.float64],
    bracket_upper: npt.NDArray[np.float64],
    settings: SolverSettings,
) -> DeliveryLoad:
    """Load the whole lines of one delivery into trucks (2D bin packing on weight and volume).

    The fewest trucks first, then the cheapest loading: the rate sheet is not subadditive at the bracket
    edges, so a pure cost minimum could send extra trucks just to land in cheaper brackets.
    """
    if (weights > settings.truck_kg).any() or (volumes > settings.truck_m3).any():
        raise ValueError("a single line does not fit in a truck")
    if weights.sum() <= settings.truck_kg and volumes.sum() <= settings.truck_m3:
        total = float(weights.sum())
        return DeliveryLoad(
            truck_of_line=np.ones(len(weights), dtype=np.int64),
            truck_weights=np.array([total]),
            truck_volumes=np.array([float(volumes.sum())]),
            truck_costs=np.array([truck_cost(total, prices, bracket_upper)]),
            status="single truck",
        )
    lower = int(np.ceil(max(weights.sum() / settings.truck_kg, volumes.sum() / settings.truck_m3)))
    for slots in range(min(len(weights), lower + 1), len(weights) + 1):
        load = _pack(weights, volumes, prices, bracket_upper, settings, slots)
        if load is not None:
            return load
    raise RuntimeError("no loading found")  # unreachable: one truck per line always fits


def best_per_profile(solved: Sequence[CoupleSolution], frequencies: Iterable[int]) -> dict[int, CoupleSolution]:
    """Best plan of each profile N among all the plans solved for a couple.

    A plan that uses m weekdays is valid for every N >= m, so the plan kept for N is the cheapest plan
    using at most N weekdays (this also makes the cost non-increasing in N, whatever the solver gap).
    """
    plans: dict[int, CoupleSolution] = {}
    for n in frequencies:
        candidates = [sol for sol in solved if len(sol.weekdays) <= n]
        if not candidates:
            raise RuntimeError(f"no plan with at most {n} weekdays")
        best = min(candidates, key=lambda sol: (sol.model_cost, len(sol.weekdays)))
        plans[n] = best if best.n_max == n else replace(best, n_max=n, seconds=0.0)
    return plans


def solve_all(
    instances: Sequence[CoupleInstance],
    frequencies: Sequence[int],
    calendar: npt.NDArray[np.datetime64],
    settings: SolverSettings,
    workers: int | None = None,
) -> dict[tuple[str, str], dict[int, CoupleSolution]]:
    """Solve every (couple, profile) problem in parallel; returns the best plan of each couple and profile."""
    workers = workers or os.cpu_count() or 1
    # Largest problems first: they set the total duration, the small ones fill the gaps
    tasks = sorted(((inst, n) for inst in instances for n in frequencies), key=lambda t: -len(t[0].line_ids) * t[1])
    solved: dict[tuple[str, str], list[CoupleSolution]] = {inst.key: [] for inst in instances}
    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(solve_couple, inst, n, calendar, settings): inst.key for inst, n in tasks}
        for done, future in enumerate(as_completed(futures), start=1):
            solved[futures[future]].append(future.result())
            if done % 150 == 0 or done == len(futures):
                logger.info("%d / %d solves done (%.0f s, %d workers)", done, len(futures),
                            time.perf_counter() - start, workers)
    return {key: best_per_profile(sols, frequencies) for key, sols in solved.items()}
