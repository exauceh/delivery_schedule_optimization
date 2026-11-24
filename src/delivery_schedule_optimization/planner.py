"""Former single-module engine, kept as a facade for the milestone notebooks (`from delivery_schedule_optimization import planner as pl`).

The code now lives in the packages `domain`, `pricing`, `preparation`, `engine`, `baseline`, `reporting` and
`validation`; this module only re-exports the same names. New code imports from those packages directly.
"""

from __future__ import annotations

from .baseline.current import current_deliveries, current_situation, load_baseline, reference_plan
from .domain.calendar import day_sets, first_working_day, horizon_weeks, is_working_day, monday, plan_start, public_holidays
from .domain.config import COUPLE, DAY_NAMES, PlannerConfig
from .domain.models import CouplePlan
from .engine.candidates import candidate_plans, couple_candidates
from .engine.graph import departure_nodes, optimal_plan
from .engine.loading import pack
from .engine.plans import delivery_dates, delivery_options, evaluate, evaluate_departures, plan_couple
from .engine.reassign import reassign
from .engine.selection import choose_profiles, selection_key
from .engine.smoothing import plant_frontier, smooth, smoothing_indicator, smoothing_options
from .io.readers import read_rate_sheet
from .preparation.lines import load_lines
from .pricing.tariff import Tariff, rate_grid
from .reporting.kpis import plant_day_load
from .reporting.outputs import couple_summary, delivery_rule, export, truck_contents
from .validation.checks import check, rg06_coverage

__all__ = [
    "COUPLE",
    "DAY_NAMES",
    "CouplePlan",
    "PlannerConfig",
    "Tariff",
    "candidate_plans",
    "check",
    "choose_profiles",
    "couple_candidates",
    "couple_summary",
    "current_deliveries",
    "current_situation",
    "day_sets",
    "delivery_dates",
    "delivery_options",
    "delivery_rule",
    "departure_nodes",
    "evaluate",
    "evaluate_departures",
    "export",
    "first_working_day",
    "horizon_weeks",
    "is_working_day",
    "load_baseline",
    "load_lines",
    "monday",
    "optimal_plan",
    "pack",
    "plan_couple",
    "plan_start",
    "plant_day_load",
    "plant_frontier",
    "public_holidays",
    "rate_grid",
    "read_rate_sheet",
    "reassign",
    "reference_plan",
    "rg06_coverage",
    "selection_key",
    "smooth",
    "smoothing_indicator",
    "smoothing_options",
    "truck_contents",
]
