"""Calendar of the plan: working days, German public holidays (RG10), weeks and sets of weekdays (RG05)."""

from __future__ import annotations

import itertools
from datetime import date, timedelta
from typing import cast

import holidays as holiday_calendars
import pandas as pd

from .config import PlannerConfig


def public_holidays(lines: pd.DataFrame, config: PlannerConfig) -> set[date]:
    years = range(lines["date"].min().year - 1, lines["date"].max().year + 1)
    return set(holiday_calendars.country_holidays(config.holiday_country, years=years))


def day_sets(config: PlannerConfig) -> list[tuple[int, ...]]:
    """Every set of weekdays whose size is an allowed profile (31 sets for 1..5)."""
    return [p for k in sorted(config.allowed_frequencies) for p in itertools.combinations(range(5), k)]


def plan_start(lines: pd.DataFrame, config: PlannerConfig) -> date:
    """First possible delivery day: `max_advance_days` before the first need (client answer), so that every need,
    the first ones included, has a profile day in its window."""
    return cast(date, lines["date"].min().date()) - timedelta(days=config.max_advance_days)


def is_working_day(day: date, holidays: set[date]) -> bool:
    return day.weekday() < 5 and day not in holidays


def first_working_day(start: date, holidays: set[date]) -> date:
    day = start
    while not is_working_day(day, holidays):
        day += timedelta(days=1)
    return day


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def horizon_weeks(lines: pd.DataFrame, start: date | None = None) -> list[date]:
    """Mondays of every week from the first need, or from `start` if earlier (start of the plan), to the last need."""
    first = monday(min(cast(date, lines["date"].min().date()), start or date.max))
    return [first + timedelta(weeks=w) for w in range((monday(lines["date"].max().date()) - first).days // 7 + 1)]
