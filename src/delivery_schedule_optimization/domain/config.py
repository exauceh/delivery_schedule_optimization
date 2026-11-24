"""Business parameters of the engine (EF12) and shared constants."""

from __future__ import annotations

from dataclasses import dataclass

DAY_NAMES = ("lundi", "mardi", "mercredi", "jeudi", "vendredi")


COUPLE = ["supplier_id", "plant"]


@dataclass(frozen=True)
class PlannerConfig:
    """All tunable parameters of the planner (EF12)."""

    truck_kg: float = 24_500.0  # RG04
    truck_m3: float = 110.4  # RG04
    margin: float = 2.3923  # RG11: freight value margin (239.23 %)
    diesel_surcharge: float = 1.1943  # RG11
    max_advance_days: int = 7  # RG03: calendar days
    allowed_frequencies: tuple[int, ...] = (1, 2, 3, 4, 5)  # RG05: profile = deliveries per week (max); 4 allowed
    holiday_country: str = "DE"  # RG10: no reception on public holidays
    # RG09 smoothing of the delivery days per plant
    smoothing: bool = True  # False = keep the RG07 choice of every couple (no smoothing)
    # intensity: extra cost accepted for smoothing, share of the cost without smoothing (0.01 = 1 %). With 0, the
    # planner only spreads the days among plans of the same cost (smoothing for free).
    smoothing_budget: float = 0.0
    smoothing_time_limit: float = 60.0  # seconds given to the solver
    objective: str = "cost"  # "cost": cheapest plan first; "trucks": fewest trucks first, then cost
    # Profile = largest number of deliveries in one week ("3 2 1 1 2 1" = profile 3). True = also prefer the
    # lowest profile before the cost (former RG07 reading); False = the profile is the result of the best plan.
    lowest_profile_first: bool = False
    # RG06 (coverage). False (default) = a need may leave with ANY planned departure of its couple within its
    # window [date - max_advance_days, date]; True = only with the latest departure on or before its date.
    strict_coverage: bool = False
    reassign_passes: int = 50  # improvement passes of the assignment when strict_coverage is False
    workers: int | None = None  # processes for the candidate plans (None = every CPU core, 1 = no parallelism)
    # Client answer "add delivery days rather than trucks": True = fewest overflowing departures before the
    # cost; False = the cost decides alone (a departure may take a second truck if this is cheaper).
    overflow_first: bool = False
    improvement_passes: int = 20  # greedy reference planner (`plan_couple`) only
    # First possible delivery day: nothing ships before the schedule is released. None = date of the first need.
    plan_start: str | None = None
    # Needs with no profile day on or after plan_start in their window: "need_day" = delivered on their own
    # date (as today), "first_day" = grouped on the first day of the plan.
    startup_delivery: str = "need_day"

    @property
    def price_factor(self) -> float:
        return (1 + self.margin) * self.diesel_surcharge


@dataclass(frozen=True)
class PreparationConfig:
    """Parameters of the data preparation (Milestone 1 cleaning, DQ01 to DQ07).

    Missing weights, volumes and distances are always completed the prudent way: a full truck and the longest
    distance of the planning suppliers (H05, H06).
    """

    # DQ03: the planning ends with one date that accumulates every need after the daily horizon (01/01/2027 in the
    # November 2026 planning). None = detected automatically: the last date of the file when it comes more than
    # `tail_gap_days` after the date before it. A date string ("2027-01-01") forces it; "" disables the rule.
    tail_date: str | None = None
    tail_gap_days: int = 7
