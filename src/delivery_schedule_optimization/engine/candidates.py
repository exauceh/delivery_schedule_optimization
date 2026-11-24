"""Candidate plans of every couple: one exact plan per set of weekdays, computed in parallel."""

from __future__ import annotations

import itertools
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date

import pandas as pd

from ..domain.calendar import plan_start
from ..domain.config import COUPLE, PlannerConfig
from ..domain.models import Candidates, CouplePlan
from ..pricing.tariff import Tariff
from .graph import optimal_plan
from .reassign import reassign

CoupleJob = tuple[pd.DataFrame, set[date], Tariff, PlannerConfig, date]  # lines, holidays, tariff, config, start


def candidate_plans(lines: pd.DataFrame, holidays: set[date], tariff: Tariff, config: PlannerConfig) -> Candidates:
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
    return {(str(key[0]), str(key[1])): plans for (key, _), plans in zip(groups, results)}


def couple_candidates(job: CoupleJob) -> list[CouplePlan]:
    """Candidate plans of ONE couple (top-level function, so that it can run in another process)."""
    group, holidays, tariff, config, start = job
    plans: dict[tuple[tuple[int, ...], tuple[date, ...]], CouplePlan] = {}
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


def lower_bound(lines: pd.DataFrame, holidays: set[date], tariff: Tariff, config: PlannerConfig) -> float:
    """Cost if every couple could leave any working day (no fixed days), same method as the real plans.
    With strict RG06 it is a lower bound (exact programme); with RG06 relaxed it is an estimate (local search)."""
    start = plan_start(lines, config)
    total = 0.0
    for _, group in lines.groupby(COUPLE, sort=True):
        plan = optimal_plan(group, tuple(range(5)), holidays, tariff, config, start, same_days=False)
        if plan is None:
            raise ValueError("lower bound: a couple has no plan even without fixed days")
        if not config.strict_coverage:  # same assignment improvement as the real plans (then an estimate, not a bound)
            plan = reassign(group, plan, holidays, tariff, config, start, same_days=False)
        total += plan.cost
    return total
