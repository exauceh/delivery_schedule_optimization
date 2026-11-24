"""Main flow of the engine: raw inputs -> preparation -> current situation -> plans -> outputs -> checks.

The pipeline has no knowledge of files, of the API or of the storage: it takes the three raw input tables and the
parameters, and returns every table and indicator of the run. The CLI, the API and the tests all go through it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import pandas as pd

from .baseline.current import current_deliveries
from .domain.calendar import public_holidays
from .domain.config import PlannerConfig, PreparationConfig
from .engine.candidates import candidate_plans
from .engine.selection import choose_profiles
from .engine.smoothing import smooth
from .io.schemas import OutputName
from .preparation.lines import prepare_lines
from .preparation.pipeline import prepare
from .pricing.tariff import Tariff, rate_grid
from .reporting.kpis import plan_kpis
from .reporting.outputs import build_outputs
from .validation.checks import check

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunConfig:
    """Parameters of one run: business rules of the engine and of the data preparation."""

    planner: PlannerConfig = field(default_factory=PlannerConfig)
    preparation: PreparationConfig = field(default_factory=PreparationConfig)


@dataclass(frozen=True)
class RawInputs:
    """The three input tables, as read from the files (see `io.readers`)."""

    schedule: pd.DataFrame
    distances: pd.DataFrame
    rate_sheet: pd.DataFrame


@dataclass(frozen=True)
class PipelineResult:
    """Everything a run produces: output tables, indicators, data accuracy, rule checks and timings."""

    tables: dict[OutputName, pd.DataFrame]
    kpis: dict[str, float]
    accuracy: dict[str, float]
    checks_passed: bool
    timings: dict[str, float]


class StageTimer:
    """Measures the duration of each stage of the pipeline (cost control: computing time per stage)."""

    def __init__(self) -> None:
        self.timings: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        logger.info("Stage %s: started", name)
        start = time.perf_counter()
        try:
            yield
        finally:
            self.timings[name] = round(time.perf_counter() - start, 3)
            logger.info("Stage %s: %.1f s", name, self.timings[name])


def run_pipeline(inputs: RawInputs, config: RunConfig) -> PipelineResult:
    """Run the whole engine on raw inputs. Same inputs and parameters give the same results (deterministic)."""
    planner = config.planner
    timer = StageTimer()
    with timer.stage("preparation"):
        prepared = prepare(inputs.schedule, inputs.distances, planner, config.preparation)
        lines = prepare_lines(prepared.consolidated, planner)
        tariff = Tariff(rate_grid(inputs.rate_sheet), planner)
        holidays = public_holidays(lines, planner)
    with timer.stage("current_situation"):
        deliveries_before, _ = current_deliveries(lines, tariff, planner)
    with timer.stage("candidate_plans"):
        candidates = candidate_plans(lines, holidays, tariff, planner)
        logger.info("%d candidate plans for %d couples", sum(map(len, candidates.values())), len(candidates))
    with timer.stage("profiles_and_smoothing"):
        unsmoothed = choose_profiles(candidates, planner)
        plans = smooth(candidates, planner)
    with timer.stage("outputs"):
        outputs = build_outputs(lines, deliveries_before, plans, unsmoothed, candidates, tariff, planner)
        kpis = plan_kpis(lines, deliveries_before, outputs.trucks, outputs.allocations)
    with timer.stage("checks"):
        checks = check(lines, plans, outputs.trucks, outputs.allocations, holidays, tariff, planner, candidates)
        checks_passed = bool(checks["respecté"].all())
        if not checks_passed:
            logger.error("Business rules violated: %s", checks.loc[~checks["respecté"], "contrôle"].tolist())
    logger.info("Run done: cost %.0f -> %.0f EUR (saving %.1f %%), %d -> %d trucks", kpis["cost_before"], kpis["cost_after"],
                kpis["saving_pct"], kpis["trucks_before"], kpis["trucks_after"])
    tables = {
        OutputName.CONSOLIDATED: prepared.consolidated,
        OutputName.ANOMALIES: prepared.anomalies,
        OutputName.CURRENT_DELIVERIES: deliveries_before,
        OutputName.PROFILES: outputs.profiles,
        OutputName.TRUCK_CONTENTS: outputs.truck_contents,
        OutputName.TRUCKS: outputs.trucks,
        OutputName.ALLOCATIONS: outputs.allocations,
        OutputName.SAVING: outputs.saving,
        OutputName.CHECKS: checks,
    }
    return PipelineResult(tables=tables, kpis=kpis, accuracy=prepared.accuracy, checks_passed=checks_passed, timings=timer.timings)
