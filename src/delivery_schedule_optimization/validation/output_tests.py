"""Milestone 4 test suites on the output files of a run (EF09): constraints, missing-data assumptions, completeness.

Each test is a function that takes the tables it checks and returns `(passed, detail)`. The first line of its docstring
is its label. The tests only read the output tables: they do not call the engine, so they check what is delivered,
independently of how it was built. `run_output_tests` runs them all on one run and returns one row per test.
A test that raises an error is a failed test.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from ..domain.calendar import first_working_day, is_working_day, plan_start
from ..domain.config import COUPLE, DAY_NAMES, MissingDistance, PlannerConfig, PreparationConfig
from ..io.schemas import profiles_by_code
from ..pricing.tariff import Tariff

logger = logging.getLogger(__name__)

TestOutcome = tuple[bool, str]  # (passed, detail)
EPS = 1e-6
RESULT_COLUMNS = ["suite", "fonction", "test", "respecté", "détail"]


# --------------------------------------------------------------------------- A2. constraints, recomputed from the outputs
def test_rg01_one_couple_per_truck(allocations: pd.DataFrame) -> TestOutcome:
    """RG01 - un seul couple fournisseur × usine par camion"""
    mixed = allocations.groupby("truck_id")[COUPLE].nunique().max(axis=1)
    return bool((mixed <= 1).all()), f"{int((mixed > 1).sum())} camion(s) mélangent deux couples"


def test_rg02_never_late(allocations: pd.DataFrame) -> TestOutcome:
    """RG02 - aucune livraison après la date de besoin"""
    late = allocations[allocations["delivery_date"] > allocations["need_date"]]
    return late.empty, f"{len(late)} ligne(s) en retard"


def test_rg03_max_advance(allocations: pd.DataFrame, max_advance: int) -> TestOutcome:
    """RG03 - avance ≤ 7 jours calendaires"""
    advance = (allocations["need_date"] - allocations["delivery_date"]).dt.days
    return bool((advance <= max_advance).all()), f"avance maximale {int(advance.max())} j"


def test_rg03_advance_column(allocations: pd.DataFrame) -> TestOutcome:
    """RG03 - la colonne advance_days = need_date − delivery_date"""
    wrong = (allocations["need_date"] - allocations["delivery_date"]).dt.days != allocations["advance_days"]
    return not bool(wrong.any()), f"{int(wrong.sum())} ligne(s) incohérente(s)"


def test_rg04_weight(allocations: pd.DataFrame, truck_kg: float) -> TestOutcome:
    """RG04 - poids ≤ 24 500 kg par camion (recalculé à partir des lignes)"""
    kg = allocations.groupby("truck_id")["weight"].sum()
    return bool((kg <= truck_kg + EPS).all()), f"camion le plus lourd {kg.max():,.0f} kg".replace(",", " ")


def test_rg04_volume(allocations: pd.DataFrame, truck_m3: float) -> TestOutcome:
    """RG04 - volume ≤ 110,4 m³ par camion (recalculé à partir des lignes)"""
    m3 = allocations.groupby("truck_id")["volume"].sum()
    return bool((m3 <= truck_m3 + EPS).all()), f"camion le plus volumineux {m3.max():.1f} m³"


def test_rg05_allowed_profiles(profiles: pd.DataFrame, allowed: tuple[int, ...]) -> TestOutcome:
    """RG05 - le profil de chaque couple fait partie des profils autorisés"""
    wrong = ~profiles_by_code(profiles)["profile"].isin(allowed)
    return not bool(wrong.any()), f"{int(wrong.sum())} couple(s) hors des profils autorisés"


def test_rg10_working_days(trucks: pd.DataFrame, holidays: set[date]) -> TestOutcome:
    """RG10 - aucune livraison un week-end ou un jour férié"""
    bad = [d for d in trucks["date"] if not is_working_day(d.date(), holidays)]
    return not bad, f"{len(bad)} camion(s) un jour non ouvré"


def test_rg11_truck_cost(trucks: pd.DataFrame, tariff: Tariff) -> TestOutcome:
    """RG11 - coût de chaque camion = grille(distance, poids) × (1 + marge) × diesel"""
    expected = tariff(trucks["distance"], trucks["weight"])
    wrong = ~np.isclose(trucks["cost"], expected)
    return not bool(wrong.any()), f"{int(wrong.sum())} camion(s) mal payés"


def test_rg11_truck_totals(trucks: pd.DataFrame, allocations: pd.DataFrame) -> TestOutcome:
    """RG11 - poids, volume et nombre de lignes de chaque camion = somme de ses lignes"""
    per_truck = allocations.groupby("truck_id").agg(kg=("weight", "sum"), m3=("volume", "sum"), n=("line_id", "size"))
    if set(per_truck.index) != set(trucks["truck_id"]):
        return False, "camions présents d'un seul côté (camions / affectations)"
    t = trucks.set_index("truck_id").loc[per_truck.index]
    same = (np.allclose(t["weight"], per_truck["kg"]) and np.allclose(t["volume"], per_truck["m3"])
            and bool((t["lines"] == per_truck["n"]).all()))
    return same, f"{len(per_truck)} camions comparés"


def profile_days(text: str) -> tuple[int, ...]:
    """« mardi + jeudi » → (1, 3)."""
    return tuple(sorted(DAY_NAMES.index(name.strip()) for name in text.split("+")))


def test_same_days_profile(allocations: pd.DataFrame, profiles: pd.DataFrame) -> TestOutcome:
    """Profil - chaque couple livre sur les jours de son profil, et son maximum de livraisons par semaine = son profil"""
    errors: list[str] = []
    by_couple = dict(tuple(allocations.groupby(COUPLE)))
    for row in profiles_by_code(profiles).itertuples(index=False):
        couple = by_couple.get((row.supplier_id, row.plant))
        if couple is None:
            errors.append(f"{row.supplier_id} × {row.plant} : aucune livraison")
            continue
        days = profile_days(str(row.profile_days))
        used = set(couple["profile_day"].dt.day_of_week)
        slots = couple.drop_duplicates("profile_day")["profile_day"]
        weekly = (slots - pd.to_timedelta(slots.dt.day_of_week, unit="D")).value_counts()
        if used != set(days) or len(days) != row.profile or weekly.max() != row.profile:
            errors.append(f"{row.supplier_id} × {row.plant} : jours {sorted(used)} au lieu de {list(days)}, "
                          f"max {weekly.max()} livraison(s) / semaine pour un profil {row.profile}")
    return not errors, "; ".join(errors[:3]) or f"{len(profiles)} couples conformes"


def test_every_delivery_on_a_profile_day(allocations: pd.DataFrame, holidays: set[date]) -> TestOutcome:
    """Profil - chaque besoin part un jour de son profil, ou le jour ouvré précédent si ce jour est férié (aucune exception)"""
    missing = allocations["profile_day"].isna()
    on_day = allocations["delivery_date"] == allocations["profile_day"]
    shifted = (allocations["delivery_date"] < allocations["profile_day"]) & allocations["profile_day"].map(
        lambda d: pd.notna(d) and d.date() in holidays)
    wrong = missing | ~(on_day | shifted)
    return not bool(wrong.any()), f"{int(wrong.sum())} ligne(s) hors d'un jour de profil"


def test_plan_start(trucks: pd.DataFrame, lines: pd.DataFrame, holidays: set[date], config: PlannerConfig) -> TestOutcome:
    """Début du plan - aucune livraison avant le premier jour ouvré du plan (7 jours avant le premier besoin)"""
    first = first_working_day(plan_start(lines, config), holidays)
    earliest = trucks["date"].min()
    return earliest.date() >= first, f"premier camion le {earliest:%d/%m}, début du plan le {first:%d/%m}"


def test_contents_match_allocations(contents: pd.DataFrame, allocations: pd.DataFrame) -> TestOutcome:
    """Sortie 2 - le contenu des camions = les affectations (lignes, quantité, poids, volume par camion × quai × matériel)"""
    keys = ["truck_id", "unloading_point", "material"]
    expected = allocations.groupby(keys).agg(need_lines=("line_id", "size"), quantity=("schedule_quantity", "sum"),
                                             weight=("weight", "sum"), volume=("volume", "sum"))
    if contents.duplicated(keys).any():
        return False, "une même clé camion × quai × matériel apparaît plusieurs fois"
    got = contents.set_index(keys)[expected.columns]
    if set(got.index) != set(expected.index):
        return False, f"{len(set(got.index) ^ set(expected.index))} clé(s) présente(s) d'un seul côté"
    got = got.loc[expected.index]
    same = bool((got["need_lines"] == expected["need_lines"]).all()) and all(
        np.allclose(got[c], expected[c]) for c in ("quantity", "weight", "volume"))
    return same, f"{len(expected)} lignes de contenu comparées"


# --------------------------------------------------------------------------- A3. missing-data assumptions
def test_h05_imputed_line_alone(allocations: pd.DataFrame) -> TestOutcome:
    """H05 - une ligne imputée « camion plein » voyage seule dans son camion"""
    imputed_trucks = allocations.loc[allocations["imputed_full_truck"].astype(bool), "truck_id"]
    sizes = allocations[allocations["truck_id"].isin(imputed_trucks)].groupby("truck_id").size()
    return bool((sizes == 1).all()), f"{len(sizes)} camion(s) avec une ligne imputée, {int((sizes > 1).sum())} partagé(s)"


def test_h06_imputed_distance(lines: pd.DataFrame, trucks: pd.DataFrame, distances: pd.DataFrame,
                              method: MissingDistance = "max") -> TestOutcome:
    """H06 - un fournisseur sans distance reçoit la distance choisie (la plus longue ou la médiane) des fournisseurs connus du planning"""
    known = distances.dropna(subset=["distance"]).drop_duplicates("supplier_id").set_index("supplier_id")["distance"]
    known = known[known.index.isin(lines["supplier_id"])]
    missing = sorted(set(lines["supplier_id"]) - set(known.index))
    if not missing:
        return True, "aucun fournisseur sans distance"
    expected = float(known.max() if method == "max" else known.median())
    in_lines = lines.loc[lines["supplier_id"].isin(missing), "distance"]
    in_trucks = trucks.loc[trucks["supplier_id"].isin(missing), "distance"]
    ok = bool(np.allclose(in_lines, expected) and np.allclose(in_trucks, expected))
    return ok, f"{', '.join(missing)} : {expected:.0f} km attendus (dataset et camions)"


# --------------------------------------------------------------------------- A4. completeness and anticipation
def test_each_line_delivered_once(allocations: pd.DataFrame) -> TestOutcome:
    """RG08 - chaque ligne est livrée une seule fois"""
    duplicated = allocations["line_id"].duplicated()
    return not bool(duplicated.any()), f"{int(duplicated.sum())} doublon(s)"


def test_no_line_forgotten(allocations: pd.DataFrame, lines: pd.DataFrame) -> TestOutcome:
    """RG08 - aucune ligne du dataset n'est oubliée"""
    missing = set(lines["line_id"]) - set(allocations["line_id"])
    return not missing, f"{len(missing)} ligne(s) non livrée(s)"


def test_quantities(allocations: pd.DataFrame, lines: pd.DataFrame) -> TestOutcome:
    """RG08 - quantité livrée = quantité demandée, par usine × matériel"""
    demand = lines.groupby(["plant", "material"])["schedule_quantity"].sum()
    delivered = allocations.groupby(["plant", "material"])["schedule_quantity"].sum().reindex(demand.index, fill_value=0)
    wrong = ~np.isclose(delivered, demand)
    return not bool(wrong.any()), f"{int(wrong.sum())} usine × matériel avec une quantité différente"


def test_need_dates(allocations: pd.DataFrame, lines: pd.DataFrame) -> TestOutcome:
    """La date de besoin de chaque ligne livrée = la date du dataset"""
    expected = lines.set_index("line_id")["date"]
    got = allocations.set_index("line_id")["need_date"].reindex(expected.index)
    wrong = got != expected
    return not bool(wrong.any()), f"{int(wrong.sum())} date(s) de besoin modifiée(s)"


def test_no_stock_out(allocations: pd.DataFrame) -> TestOutcome:
    """Anticipation - à chaque date, quantité reçue cumulée ≥ besoins cumulés (usine × matériel)"""
    keys = ["plant", "material", "day"]
    received = allocations.groupby(["plant", "material", "delivery_date"])["schedule_quantity"].sum().rename_axis(keys)
    needed = allocations.groupby(["plant", "material", "need_date"])["schedule_quantity"].sum().rename_axis(keys)
    flows = pd.concat([received.rename("received"), needed.rename("needed")], axis=1).fillna(0)
    flows = flows.sort_index().groupby(level=[0, 1]).cumsum()
    short = flows[flows["received"] < flows["needed"] - EPS]
    return short.empty, f"{len(short)} jour(s) en rupture"


# --------------------------------------------------------------------------- A5. consistency of the savings
def test_cost_before(saving: pd.DataFrame, deliveries_before: pd.DataFrame) -> TestOutcome:
    """Sortie 3 - coût avant = situation actuelle recalculée camion par camion"""
    total, expected = float(saving["cost_before"].sum()), float(deliveries_before["cost"].sum())
    return bool(np.isclose(total, expected)), f"{total:,.2f} EUR (situation actuelle {expected:,.2f} EUR)".replace(",", " ")


def test_cost_after(saving: pd.DataFrame, trucks: pd.DataFrame) -> TestOutcome:
    """Sortie 3 - coût après = somme des camions de la sortie 2"""
    total, expected = float(saving["cost_after"].sum()), float(trucks["cost"].sum())
    return bool(np.isclose(total, expected)), f"{total:,.2f} EUR (camions {expected:,.2f} EUR)".replace(",", " ")


# --------------------------------------------------------------------------- runner
@dataclass(frozen=True)
class RunTables:
    """What the output tests read: the planned lines, the raw distances and the output tables of one run."""

    lines: pd.DataFrame  # consolidated dataset, as planned (line_id, imputed_full_truck)
    distances: pd.DataFrame  # raw distances file (H06)
    deliveries_before: pd.DataFrame  # current situation, one row per delivery
    profiles: pd.DataFrame  # output 1
    trucks: pd.DataFrame  # output 2
    allocations: pd.DataFrame  # output 2
    contents: pd.DataFrame  # output 2
    saving: pd.DataFrame  # output 3


def output_tests(run: RunTables, holidays: set[date], tariff: Tariff, config: PlannerConfig,
                 preparation: PreparationConfig | None = None) -> list[tuple[str, Callable[..., TestOutcome], tuple[object, ...]]]:
    """Every output test with its suite and its arguments, in the order of the Milestone 4 notebook."""
    preparation = preparation or PreparationConfig()
    a = run.allocations
    return [
        ("A2 - contraintes", test_rg01_one_couple_per_truck, (a,)),
        ("A2 - contraintes", test_rg02_never_late, (a,)),
        ("A2 - contraintes", test_rg03_max_advance, (a, config.max_advance_days)),
        ("A2 - contraintes", test_rg03_advance_column, (a,)),
        ("A2 - contraintes", test_rg04_weight, (a, config.truck_kg)),
        ("A2 - contraintes", test_rg04_volume, (a, config.truck_m3)),
        ("A2 - contraintes", test_rg05_allowed_profiles, (run.profiles, config.allowed_frequencies)),
        ("A2 - contraintes", test_rg10_working_days, (run.trucks, holidays)),
        ("A2 - contraintes", test_rg11_truck_cost, (run.trucks, tariff)),
        ("A2 - contraintes", test_rg11_truck_totals, (run.trucks, a)),
        ("A2 - contraintes", test_same_days_profile, (a, run.profiles)),
        ("A2 - contraintes", test_every_delivery_on_a_profile_day, (a, holidays)),
        ("A2 - contraintes", test_plan_start, (run.trucks, run.lines, holidays, config)),
        ("A2 - contraintes", test_contents_match_allocations, (run.contents, a)),
        ("A3 - données manquantes", test_h05_imputed_line_alone, (a,)),
        ("A3 - données manquantes", test_h06_imputed_distance, (run.lines, run.trucks, run.distances, preparation.missing_distance)),
        ("A4 - exhaustivité", test_each_line_delivered_once, (a,)),
        ("A4 - exhaustivité", test_no_line_forgotten, (a, run.lines)),
        ("A4 - exhaustivité", test_quantities, (a, run.lines)),
        ("A4 - exhaustivité", test_need_dates, (a, run.lines)),
        ("A4 - exhaustivité", test_no_stock_out, (a,)),
        ("A5 - saving", test_cost_before, (run.saving, run.deliveries_before)),
        ("A5 - saving", test_cost_after, (run.saving, run.trucks)),
    ]


def run_test(suite: str, fn: Callable[..., TestOutcome], *args: object) -> dict[str, object]:
    """Run one test function; an error is a failed test, logged with its stack trace."""
    try:
        passed, detail = fn(*args)
    except Exception as error:  # a crash is a failed test, never a crashed run
        logger.exception("Output test %s raised an error", fn.__name__)
        passed, detail = False, f"{type(error).__name__}: {error}"
    label = (fn.__doc__ or fn.__name__).strip().splitlines()[0]
    return {"suite": suite, "fonction": fn.__name__, "test": label, "respecté": bool(passed), "détail": detail}


def run_output_tests(run: RunTables, holidays: set[date], tariff: Tariff, config: PlannerConfig,
                     preparation: PreparationConfig | None = None) -> pd.DataFrame:
    """Run every output test on one run: one row per test (suite, function, label, passed, detail)."""
    tests = output_tests(run, holidays, tariff, config, preparation)
    results = pd.DataFrame([run_test(suite, fn, *args) for suite, fn, args in tests],
                           columns=RESULT_COLUMNS)
    failed = results.loc[~results["respecté"], "fonction"].tolist()
    if failed:
        logger.error("Output tests failed: %s", failed)
    else:
        logger.info("Output tests: %d passed", len(results))
    return results
