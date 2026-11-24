"""Choice of the plan, and so of the profile, of each couple without smoothing (RG07)."""

from __future__ import annotations

from ..domain.config import PlannerConfig
from ..domain.models import Candidates, CouplePlan, Plans, SortKey


def selection_key(plan: CouplePlan, config: PlannerConfig = PlannerConfig()) -> SortKey:
    """Choice of the plan of a couple (RG07 + client answer "add delivery days rather than trucks"):
    with `overflow_first`, fewest overflowing departures first; then the cheapest; among plans of the same cost, the
    lowest profile (fewest deliveries per week); then fewest trucks, least advance, earliest days of the week.
    The profile itself is always the largest number of deliveries made in one week."""
    overflow = (plan.overflow_departures,) if config.overflow_first else ()
    return (*overflow, round(plan.cost, 6), plan.frequency, plan.n_trucks, plan.overflow_departures, plan.total_advance,
            plan.profile)


def choose_profiles(candidates: Candidates, config: PlannerConfig = PlannerConfig()) -> Plans:
    """Plan (and so profile) of each couple without smoothing (RG07)."""
    return {key: min(plans, key=lambda p: selection_key(p, config)) for key, plans in candidates.items()}
