"""Choice of the plan, and so of the profile, of each couple without smoothing (RG07)."""

from __future__ import annotations

from ..domain.config import PlannerConfig
from ..domain.models import Candidates, CouplePlan, Plans, SortKey


def selection_key(plan: CouplePlan, config: PlannerConfig = PlannerConfig()) -> SortKey:
    """Choice of the plan of a couple (RG07 + client answer "add delivery days rather than trucks"):
    fewest overflowing departures first; then, with `lowest_profile_first`, the lowest profile; then the
    objective (cheapest or fewest trucks); then least advance, then earliest days of the week.
    The profile itself is always the largest number of deliveries made in one week."""
    cost, trucks = round(plan.cost, 6), plan.n_trucks
    main = (trucks, cost) if config.objective == "trucks" else (cost, trucks)
    lowest = (plan.frequency,) if config.lowest_profile_first else ()
    overflow = (plan.overflow_departures,) if config.overflow_first else ()
    return (*overflow, *lowest, *main, plan.overflow_departures, plan.total_advance, plan.frequency, plan.profile)


def choose_profiles(candidates: Candidates, config: PlannerConfig = PlannerConfig()) -> Plans:
    """Plan (and so profile) of each couple without smoothing (RG07)."""
    return {key: min(plans, key=lambda p: selection_key(p, config)) for key, plans in candidates.items()}
