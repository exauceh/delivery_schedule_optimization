"""Candidate plans of every couple: one exact plan per set of weekdays, computed in parallel."""

from __future__ import annotations

import itertools
from datetime import date

import pandas as pd

from ..domain.calendar import plan_start
from ..domain.config import COUPLE, PlannerConfig
from ..domain.models import Candidates, CouplePlan
from ..pricing.tariff import Tariff
from .graph import optimal_plan
from .parallel import parallel_map, worker_count
from .reassign import reassign

CoupleJob = tuple[pd.DataFrame, set[date], Tariff, PlannerConfig, date]  # lines, holidays, tariff, config, start


def candidate_plans(lines: pd.DataFrame, holidays: set[date], tariff: Tariff, config: PlannerConfig) -> Candidates:
    """Best valid plan of every couple on each set of days (exact dynamic programme), duplicates removed.

    The couples are independent (RG01): they are spread over `workers` processes. The result does not
    depend on the number of processes.
    """
    start = plan_start(lines, config)
    groups = list(lines.groupby(COUPLE, sort=True))
    jobs = [(group, holidays, tariff, config, start) for _, group in groups]
    workers = worker_count(config.workers, len(jobs))
    results = parallel_map(couple_candidates, jobs, workers, chunksize=max(1, len(jobs) // (4 * workers)))
    return {(str(key[0]), str(key[1])): plans for (key, _), plans in zip(groups, results)}


def couple_candidates(job: CoupleJob) -> list[CouplePlan]:
    """Candidate plans of ONE couple (top-level function, so that it can run in another process)."""
    group, holidays, tariff, config, start = job
    plans: dict[tuple[tuple[int, ...], tuple[date, ...]], CouplePlan] = {}
    for size in sorted(config.allowed_frequencies):
        for days in itertools.combinations(range(5), size):
            plan = optimal_plan(group, days, holidays, tariff, config, start)
            if plan is not None and not config.strict_coverage:
                plan = reassign(group, plan, tariff, config)
            if plan is not None:
                plans.setdefault((plan.profile, tuple(plan.dates.tolist())), plan)
    return list(plans.values())

