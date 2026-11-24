"""Choice of the plan of a couple (RG07): cheapest first, then the lowest profile; smoothing keeps that profile."""

from __future__ import annotations

from datetime import date

import numpy as np

from delivery_schedule_optimization.domain.config import PlannerConfig
from delivery_schedule_optimization.domain.models import CouplePlan
from delivery_schedule_optimization.engine.selection import choose_profiles
from delivery_schedule_optimization.engine.smoothing import smoothing_options
from delivery_schedule_optimization.validation.checks import rg07_profile

KEY = ("SUP", "PLT")
MONDAY, TUESDAY = date(2026, 11, 9), date(2026, 11, 10)


def _plan(cost: float, days: tuple[int, ...], trucks: int, advance: int) -> CouplePlan:
    """A plan delivering every week on `days` (profile = len(days)) during two weeks."""
    weekly = {MONDAY: len(days), date(2026, 11, 16): len(days)}
    return CouplePlan(allowed_days=days, profile=days, slots=[], dates=np.array([], dtype="datetime64[D]"),
                      trucks=[(np.datetime64(MONDAY), [0])] * trucks, truck_weights=np.ones(trucks), cost=cost,
                      overflow_departures=0, total_advance=advance, weekly_deliveries=weekly)


def test_the_cheapest_plan_wins_whatever_its_profile() -> None:
    cheap_profile_2, dear_profile_1 = _plan(100.0, (0, 2), 4, 10), _plan(100.5, (0,), 4, 30)
    assert choose_profiles({KEY: [dear_profile_1, cheap_profile_2]})[KEY] is cheap_profile_2


def test_same_cost_the_lowest_profile_wins_before_trucks_and_advance() -> None:
    profile_2, profile_1 = _plan(100.0, (0, 2), 3, 10), _plan(100.0, (0,), 4, 30)
    assert choose_profiles({KEY: [profile_2, profile_1]})[KEY] is profile_1
    assert rg07_profile([profile_2, profile_1], PlannerConfig()) == 1


def test_smoothing_only_offers_plans_of_the_chosen_profile() -> None:
    monday, tuesday, both = _plan(100.0, (0,), 4, 30), _plan(100.0, (1,), 4, 30), _plan(100.0, (0, 1), 4, 10)
    candidates = {KEY: [both, monday, tuesday]}
    options = smoothing_options(candidates, choose_profiles(candidates))
    assert {p.profile for p in options[KEY]} == {(0,), (1,)}
