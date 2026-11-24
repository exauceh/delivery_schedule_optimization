"""Milestone 4 unit cases of the engine: each function runs one engine function on a small case whose answer is known.

Same convention as `output_tests`: a case returns `(passed, detail)` and the first line of its docstring is its label.
The cases are run by the test suite (pytest) and shown in the Milestone 4 notebook; a run of the application does not
replay them (they test the engine, not one run).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from ..domain.calendar import day_sets, first_working_day, is_working_day, plan_start
from ..domain.config import COUPLE, PlannerConfig
from ..engine.candidates import couple_candidates
from ..engine.graph import optimal_plan
from ..engine.loading import pack
from ..engine.plans import delivery_options
from ..engine.reassign import reassign
from ..pricing.tariff import Tariff
from .output_tests import EPS, RESULT_COLUMNS, TestOutcome, run_test

EXAMPLE_COUPLE = ("SUP_00085", "PLT_004")  # couple of the project brief: 121 km, needs every working day


# --------------------------------------------------------------------------- A1. engine functions on hand-made cases
def test_saturday_is_not_a_working_day(holidays: set[date]) -> TestOutcome:
    """is_working_day : un samedi n'est pas ouvré"""
    return not is_working_day(date(2026, 11, 21), holidays), "samedi 21/11"


def test_christmas_is_a_holiday(holidays: set[date]) -> TestOutcome:
    """is_working_day : le 25/12 est férié en Allemagne"""
    return not is_working_day(date(2026, 12, 25), holidays), "vendredi 25/12"


def test_first_working_day_after_a_saturday(holidays: set[date]) -> TestOutcome:
    """first_working_day : un samedi donne le lundi suivant"""
    day = first_working_day(date(2026, 11, 21), holidays)
    return day == date(2026, 11, 23), f"{day:%d/%m}"


def test_window_of_a_monday_profile(holidays: set[date], max_advance: int) -> TestOutcome:
    """delivery_options : besoin du vendredi 20/11, profil lundi → livré le lundi 16/11"""
    options = delivery_options(date(2026, 11, 20), (0,), holidays, max_advance)
    return options == [(date(2026, 11, 16), date(2026, 11, 16))], str(options)


def test_window_of_7_days(holidays: set[date], max_advance: int) -> TestOutcome:
    """delivery_options : aucune option à plus de 7 jours d'avance, ni après la date du besoin"""
    need = date(2026, 11, 27)
    options = delivery_options(need, (0, 1, 2, 3, 4), holidays, max_advance)
    advances = [(need - d).days for _, d in options]
    return bool(options) and all(0 <= a <= max_advance for a in advances), f"avances possibles {advances} j"


def test_every_need_has_a_profile_day(holidays: set[date], max_advance: int) -> TestOutcome:
    """delivery_options : avec 7 jours d'avance, chaque jour de besoin a une option pour chacun des 5 profils d'un jour"""
    needs = pd.date_range("2026-11-09", "2026-12-18", freq="B").date
    uncovered = [(need, day) for need in needs for day in range(5) if not delivery_options(need, (day,), holidays, max_advance)]
    return not uncovered, f"{len(uncovered)} besoin(s) sans jour de profil dans leur fenêtre"


def test_pack_two_trucks(config: PlannerConfig) -> TestOutcome:
    """pack : capacité respectée, chaque ligne une fois, nombre de camions minimal (2)"""
    weight, volume = np.array([20_000.0, 10_000.0, 4_000.0, 500.0]), np.array([10.0, 50.0, 60.0, 1.0])
    packed = pack(weight, volume, config)
    within = all(weight[t].sum() <= config.truck_kg and volume[t].sum() <= config.truck_m3 for t in packed)
    every_line_once = sorted(i for t in packed for i in t) == list(range(len(weight)))
    return within and every_line_once and len(packed) == 2, f"{len(packed)} camions : {packed}"


def test_reassign_never_worse(lines: pd.DataFrame, holidays: set[date], tariff: Tariff, config: PlannerConfig,
                              n_couples: int = 10, max_profile: int = 2) -> TestOutcome:
    """reassign : n'augmente jamais le coût d'un plan et garde un plan valide (fenêtre, capacité, mêmes lignes)"""
    start = plan_start(lines, config)
    first_day = first_working_day(start, holidays)
    biggest = lines.groupby(COUPLE).size().nlargest(n_couples).index  # the couples with most lines: most moves
    sets = [d for d in day_sets(config) if len(d) <= max_profile]
    checked = improved = 0
    for key in biggest:
        group = lines[(lines["supplier_id"] == key[0]) & (lines["plant"] == key[1])]
        due = group["date"].to_numpy(dtype="datetime64[D]")
        weight, volume = group["weight"].to_numpy(float), group["volume"].to_numpy(float)
        for days in sets:
            plan = optimal_plan(group, days, holidays, tariff, config, start)
            if plan is None:
                continue
            after = reassign(group, plan, tariff, config)
            delivered = after.dates.astype("datetime64[D]")
            advance = (due - delivered).astype(int)
            positions = sorted(i for _, members in after.trucks for i in members)
            capacity = all(weight[m].sum() <= config.truck_kg + EPS and volume[m].sum() <= config.truck_m3 + EPS
                           for _, m in after.trucks)
            if after.cost > plan.cost + EPS:
                return False, f"{key} jours {days} : {plan.cost:.2f} → {after.cost:.2f} EUR"
            if not (advance.min() >= 0 and advance.max() <= config.max_advance_days):
                return False, f"{key} jours {days} : fenêtre de livraison violée"
            if positions != list(range(len(group))) or not capacity:
                return False, f"{key} jours {days} : ligne perdue / dupliquée ou camion trop chargé"
            if delivered.min() < np.datetime64(first_day):
                return False, f"{key} jours {days} : livraison avant le début du plan"
            checked += 1
            improved += after.cost < plan.cost - EPS
    return checked > 0, f"{checked} plans testés, {improved} améliorés, aucun dégradé"


# --------------------------------------------------------------------------- A3. edge cases
def test_full_line_fits_one_truck(config: PlannerConfig) -> TestOutcome:
    """H05 - une ligne de 24 500 kg exactement tient dans un seul camion"""
    trucks = len(pack(np.array([config.truck_kg]), np.array([1.0]), config))
    return trucks == 1, f"{trucks} camion(s)"


def test_fictitious_holiday(lines: pd.DataFrame, holidays: set[date], tariff: Tariff, config: PlannerConfig) -> TestOutcome:
    """RG10 - un férié fictif le mardi 17/11 (profil mardi) : la livraison est avancée au lundi 16/11, dans la fenêtre"""
    fake = holidays | {date(2026, 11, 17)}
    couple = lines[lines["supplier_id"].eq(EXAMPLE_COUPLE[0]) & lines["plant"].eq(EXAMPLE_COUPLE[1])]
    plan = optimal_plan(couple, (1,), fake, tariff, config, plan_start(lines, config))
    if plan is None:
        return False, "aucun plan sur le mardi"
    due = couple["date"].to_numpy(dtype="datetime64[D]")
    delivered = plan.dates.astype("datetime64[D]")
    week = (due >= np.datetime64("2026-11-17")) & (due <= np.datetime64("2026-11-23"))
    advance = (due - delivered).astype(int)
    moved = bool(week.any()) and bool((delivered[week] == np.datetime64("2026-11-16")).all())
    never_on_holiday = not bool((delivered == np.datetime64("2026-11-17")).any())
    in_window = bool(((advance >= 0) & (advance <= config.max_advance_days)).all())
    return moved and never_on_holiday and in_window, (
        f"{int(week.sum())} ligne(s) de la semaine du 17/11 livrée(s) le {', '.join(sorted({str(d) for d in delivered[week]}))}")


def test_couple_with_one_line(lines: pd.DataFrame, holidays: set[date], tariff: Tariff, config: PlannerConfig) -> TestOutcome:
    """Couple d'une seule ligne : chaque plan candidat a un seul camion, un profil 1 et le prix de la grille"""
    one = lines[lines["date"] >= lines["date"].min() + pd.Timedelta(days=14)].head(1)
    plans = couple_candidates((one, holidays, tariff, config, plan_start(one, config)))
    price = float(tariff(one["distance"].iloc[0], one["weight"].iloc[0]))
    ok = bool(plans) and all(p.n_trucks == 1 and p.frequency == 1 and np.isclose(p.cost, price) for p in plans)
    return ok, f"{len(plans)} plan(s) candidat(s), prix attendu {price:.2f} EUR"


def test_first_week_on_the_profile(lines: pd.DataFrame, holidays: set[date], tariff: Tariff, config: PlannerConfig) -> TestOutcome:
    """Début du plan - un besoin du lundi 09/11 pour un couple livré le mercredi part le mercredi 04/11 (aucun démarrage)"""
    couple = lines[lines["supplier_id"].eq(EXAMPLE_COUPLE[0]) & lines["plant"].eq(EXAMPLE_COUPLE[1])]
    plan = optimal_plan(couple, (2,), holidays, tariff, config, plan_start(lines, config))
    if plan is None:
        return False, "aucun plan sur le mercredi"
    first_need = couple["date"].min().date()
    delivered = pd.Timestamp(plan.dates[couple["date"].dt.date.to_numpy() == first_need][0]).date()
    slots_ok = all(slot.weekday() == 2 for slot in plan.slots)
    return delivered == date(2026, 11, 4) and slots_ok, f"besoin du {first_need:%d/%m} livré le {delivered:%d/%m}"


CASES: list[tuple[str, Callable[..., TestOutcome], tuple[str, ...]]] = [  # (suite, case, names of its arguments)
    ("A1 - unitaires", test_saturday_is_not_a_working_day, ("holidays",)),
    ("A1 - unitaires", test_christmas_is_a_holiday, ("holidays",)),
    ("A1 - unitaires", test_first_working_day_after_a_saturday, ("holidays",)),
    ("A1 - unitaires", test_window_of_a_monday_profile, ("holidays", "max_advance")),
    ("A1 - unitaires", test_window_of_7_days, ("holidays", "max_advance")),
    ("A1 - unitaires", test_every_need_has_a_profile_day, ("holidays", "max_advance")),
    ("A1 - unitaires", test_pack_two_trucks, ("config",)),
    ("A1 - unitaires", test_reassign_never_worse, ("lines", "holidays", "tariff", "config")),
    ("A3 - cas limites", test_full_line_fits_one_truck, ("config",)),
    ("A3 - cas limites", test_fictitious_holiday, ("lines", "holidays", "tariff", "config")),
    ("A3 - cas limites", test_couple_with_one_line, ("lines", "holidays", "tariff", "config")),
    ("A3 - cas limites", test_first_week_on_the_profile, ("lines", "holidays", "tariff", "config")),
]


@dataclass(frozen=True)
class EngineInputs:
    """A planning prepared for the engine: planned lines, public holidays, price of a truck, parameters."""

    lines: pd.DataFrame
    holidays: set[date]
    tariff: Tariff
    config: PlannerConfig


def case_arguments(names: tuple[str, ...], inputs: EngineInputs) -> tuple[object, ...]:
    """Arguments of one case, from the names of its parameters."""
    available: dict[str, object] = {"lines": inputs.lines, "holidays": inputs.holidays, "tariff": inputs.tariff,
                                    "config": inputs.config, "max_advance": inputs.config.max_advance_days}
    return tuple(available[name] for name in names)


def run_engine_cases(inputs: EngineInputs) -> pd.DataFrame:
    """Run every engine case: one row per case (suite, function, label, passed, detail), like `run_output_tests`."""
    rows = [run_test(suite, fn, *case_arguments(names, inputs)) for suite, fn, names in CASES]
    return pd.DataFrame(rows, columns=RESULT_COLUMNS)
