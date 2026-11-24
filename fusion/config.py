"""Every tunable parameter of the engine, in one place (nothing else is hard-coded)."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    consolidated_csv: Path | None = Path("data/consolidated_dataset.csv")  # milestone 1 output; None = prepare the raw files here
    m2_baseline_eur: float | None = 176_979.32            # milestone 2 baseline cost (its estimator), checked at start
    schedule_csv: Path = Path("docs/released_schedule.csv")
    distances_csv: Path = Path("docs/distances.csv")
    rate_sheet_csv: Path = Path("docs/rate_sheet.csv")
    output_dir: Path = Path("sorties")

    # data rules (milestone 1)
    cumulative_dates: tuple[str, ...] = ("2027-01-01",)   # rule 2: cumulative line, not a need
    drop_lines_over_capacity: bool = True                 # rule 4: a line heavier than a truck is removed (client request)
    missing_load: str = "full_truck"                      # rule 5: "full_truck" or "median" (couple, then plant)
    missing_distance: str = "max"                         # rule 6: "max" or "median"

    # business rules
    truck_kg: float = 24_500.0                            # rule 13
    truck_m3: float = 110.4                               # rule 13
    anticipation_days: int = 7                            # rule 12, calendar days
    holiday_country: str = "DE"                           # rules 7-8
    deliver_before_first_need: bool = True                # rule 9: False = nothing leaves before the first need (09/11); True = first need - anticipation (02/11)
    margin: float = 2.3923                                # rule 19
    diesel_surcharge: float = 1.1943                      # rule 19
    frequencies: tuple[int, ...] = (1, 2, 3, 4, 5)        # rule 22

    # smoothing (rules 27-29)
    smoothing_max_extra_cost: float = 0.0                 # rule 28: extra cost allowed per plant; 0 = minimal cost, the peak is lowered only for free
    solver_deterministic_time: float = 10.0               # CP-SAT budget in deterministic units: same input -> same output
    solver_seed: int = 42
    plan_variant_tolerance: float = 0.02                  # extra plans: other reference weeks within +2 % of the best (more choice for the smoothing)
    polish_deterministic_time: float = 1.0                # CP-SAT budget per couple for the final exact re-optimisation
    workers: int | None = None                            # processes for the plan generation; None = every core

    # teacher's targets (checked, reported apart from the rule controls)
    target_seconds: float = 90.0
    target_saving_eur: float = 40_000.0
    target_max_trucks: int = 800
