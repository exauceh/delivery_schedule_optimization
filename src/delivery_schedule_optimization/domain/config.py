"""Business parameters of the engine (EF12) and shared constants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DAY_NAMES = ("lundi", "mardi", "mercredi", "jeudi", "vendredi")


COUPLE = ["supplier_id", "plant"]

# How missing data are completed (DQ04, DQ07). The first value of each is the prudent choice of Milestone 1 (H05, H06).
MissingLoad = Literal["full_truck", "couple_median"]  # a full truck / median line of the same supplier x plant couple
MissingDistance = Literal["max", "median"]  # longest / median distance of the planning suppliers


@dataclass(frozen=True)
class PlannerConfig:
    """All tunable parameters of the planner (EF12).

    The freight margin and the diesel surcharge (RG11) are not parameters: they are terms of the carrier's rate sheet
    and are always read from its PDF, with the price grid (`io.schemas.RateSheet`).
    """

    truck_kg: float = 24_500.0  # RG04
    truck_m3: float = 110.4  # RG04
    max_advance_days: int = 7  # RG03: calendar days
    allowed_frequencies: tuple[int, ...] = (1, 2, 3, 4, 5)  # RG05: profile = deliveries per week (max); 4 allowed
    holiday_country: str = "DE"  # RG10: no reception on public holidays
    # RG09 smoothing of the delivery days per plant
    smoothing: bool = True  # False = keep the RG07 choice of every couple (no smoothing)
    # intensity: extra cost accepted for smoothing, share of the cost without smoothing (0.01 = 1 %). With 0, the
    # planner only spreads the days among plans of the same cost (smoothing for free).
    smoothing_budget: float = 0.0
    smoothing_time_limit: float = 60.0  # seconds given to the solver
    # RG06 (coverage). False (default) = a need may leave with ANY planned departure of its couple within its
    # window [date - max_advance_days, date]; True = only with the latest departure on or before its date.
    strict_coverage: bool = False
    reassign_passes: int = 50  # improvement passes of the assignment when strict_coverage is False
    workers: int | None = None  # processes for the candidate plans and the smoothing of the plants (None = every CPU core, 1 = no parallelism)
    # Client answer "add delivery days rather than trucks": True = fewest overflowing departures before the
    # cost; False = the cost decides alone (a departure may take a second truck if this is cheaper).
    overflow_first: bool = False


@dataclass(frozen=True)
class PreparationConfig:
    """Parameters of the data preparation (Milestone 1 cleaning, DQ01 to DQ07).

    By default, missing weights, volumes and distances are completed the prudent way: a full truck and the longest
    distance of the planning suppliers (H05, H06). The interface asks the user to choose when data are missing.
    """

    # DQ04: missing or non-positive weight / volume of a line. "full_truck" = the capacity of one truck (the line travels
    # alone); "couple_median" = the median weight (volume) of the known lines of the same supplier x plant couple, else a
    # full truck. (The weight of a line is not proportional to its quantity, and most materials with a missing weight
    # have no known weight at all: the couple is the reference.)
    missing_load: MissingLoad = "full_truck"
    # DQ07: supplier of the planning without a distance. "max" = the longest, "median" = the median distance of the
    # planning suppliers that have one.
    missing_distance: MissingDistance = "max"

    # DQ03: the planning ends with one date that accumulates every need after the daily horizon (01/01/2027 in the
    # November 2026 planning). None = detected automatically: the last date of the file when it comes more than
    # `tail_gap_days` after the date before it. A date string ("2027-01-01") forces it; "" disables the rule.
    tail_date: str | None = None
    tail_gap_days: int = 7
