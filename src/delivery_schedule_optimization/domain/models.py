"""Plan of one supplier x plant couple, as produced by the engine."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import numpy as np

CoupleKey = tuple[str, str]  # (supplier_id, plant)
Option = tuple[date, date]  # (profile day, delivery date)
SortKey = tuple[float | tuple[int, ...], ...]  # lexicographic criteria of a plan


@dataclass
class CouplePlan:
    allowed_days: tuple[int, ...]  # set of days evaluated
    profile: tuple[int, ...]  # days actually used (= the fixed delivery days of the couple)
    slots: list[date]  # profile day served by each line
    dates: np.ndarray  # delivery date of each line of the couple
    trucks: list[tuple[np.datetime64, list[int]]]  # (date, positions of the couple's lines)
    truck_weights: np.ndarray
    cost: float
    overflow_departures: int  # departures whose MEASURED lines need more than one truck
    total_advance: int
    weekly_deliveries: dict[date, int]  # Monday -> number of profile days used that week

    @property
    def frequency(self) -> int:
        """Profile = largest number of deliveries made in one week over the period."""
        return max(self.weekly_deliveries.values(), default=0)

    @property
    def n_trucks(self) -> int:
        return len(self.trucks)

    @property
    def trucks_per_date(self) -> dict[np.datetime64, int]:
        count: dict[np.datetime64, int] = defaultdict(int)
        for d, _ in self.trucks:
            count[d] += 1
        return count

    @property
    def same_days(self) -> bool:
        """Business rule: deliveries always fall on the same set of days, whose size is the profile."""
        return len(self.profile) == self.frequency


Plans = dict[CoupleKey, CouplePlan]  # one plan per couple
Candidates = dict[CoupleKey, list[CouplePlan]]  # candidate plans of every couple
