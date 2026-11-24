"""Automatic checks of the business rules on a finished plan (EF09). Completed by milestone 4 (test suites)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import cast

import numpy as np
import pandas as pd

from ..domain.calendar import first_working_day, is_working_day, plan_start
from ..domain.config import COUPLE, PlannerConfig
from ..domain.models import Candidates, Plans
from ..engine.graph import departure_nodes
from ..pricing.tariff import Tariff


def first_profile_day(profile: tuple[int, ...], start: pd.Timestamp, holidays: set[date]) -> pd.Timestamp:
    """First delivery day of a profile on or after the start of the plan (RG10 shift included)."""
    nodes = departure_nodes([start.date() + timedelta(days=13)], profile, holidays, start.date())
    return pd.Timestamp(min(d for s, d in nodes if s is not None))


def rg06_coverage(allocations: pd.DataFrame) -> bool:
    """RG06: the departure of a need is the latest departure of its couple on or before the need date."""
    for _, group in allocations.groupby(COUPLE):
        departures = np.sort(group["delivery_date"].unique())
        latest = departures[np.searchsorted(departures, group["need_date"].to_numpy(), side="right") - 1]
        if (latest != group["delivery_date"].to_numpy()).any():
            return False
    return True


def check(lines: pd.DataFrame, plans: Plans, trucks: pd.DataFrame, allocations: pd.DataFrame,
          holidays: set[date], tariff: Tariff, config: PlannerConfig, candidates: Candidates | None = None) -> pd.DataFrame:
    """Verify every business rule on a finished plan, independently of how it was built.

    One row per check: rule, what is verified, True = respected. RG07 is checked only when the
    candidate plans are given.
    """
    start = pd.Timestamp(first_working_day(plan_start(lines, config), holidays))
    alloc = allocations.copy()
    per_truck = allocations.groupby("truck_id").agg(kg=("weight", "sum"), m3=("volume", "sum"), lines=("line_id", "size"))
    t = trucks.set_index("truck_id")
    demand = lines.groupby(["plant", "material"])["schedule_quantity"].sum()
    delivered = allocations.groupby(["plant", "material"])["schedule_quantity"].sum()
    served = alloc.dropna(subset=["profile_day"])  # needs delivered on a profile day (start-up excluded)
    days_used = served.groupby(COUPLE)["profile_day"].apply(lambda s: set(s.dt.day_of_week))
    max_per_week = (served.drop_duplicates([*COUPLE, "profile_day"])
                    .assign(week=lambda d: d["profile_day"] - pd.to_timedelta(d["profile_day"].dt.day_of_week, unit="D"))
                    .groupby([*COUPLE, "week"]).size().groupby(level=[0, 1]).max())
    shifted = served[served["delivery_rule"].eq("férié avancé")]
    rows = [
        ("RG01", "Toutes les lignes d'un camion ont le même fournisseur et la même usine",
         allocations.groupby("truck_id")[COUPLE].nunique().max().max() == 1),
        ("RG02", "Aucune ligne n'est livrée après sa date de besoin (avance ≥ 0)",
         (alloc["advance_days"] >= 0).all()),
        ("RG03", f"Aucune ligne n'est livrée plus de {config.max_advance_days} jours calendaires avant sa date",
         (alloc["advance_days"] <= config.max_advance_days).all()),
        ("RG04", f"Somme des poids ≤ {f'{config.truck_kg:,.0f}'.replace(',', ' ')} kg et somme des volumes ≤ "
                 f"{str(config.truck_m3).replace('.', ',')} m³, camion par camion, recalculées à partir des lignes",
         (per_truck["kg"] <= config.truck_kg + 1e-6).all() and (per_truck["m3"] <= config.truck_m3 + 1e-6).all()),
        ("Profil - mêmes jours", "Chaque couple livre toujours sur le même ensemble de jours, et le nombre de jours de "
                                 "cet ensemble = le maximum de livraisons faites en une semaine (= profil affiché)",
         all(len(days_used[k]) == max_per_week[k] == plans[k].frequency and days_used[k] == set(plans[k].profile)
             for k in days_used.index)),
        ("RG05 - profil", "Le profil de chaque couple fait partie des profils autorisés",
         all(p.frequency in config.allowed_frequencies for p in plans.values())),
        ("RG05 - jours", "Chaque livraison tombe un jour du profil du couple, sauf démarrage (première semaine) "
                         "ou jour férié avancé",
         (served.loc[served["delivery_rule"].eq("jour du profil"), "delivery_date"]
          == served.loc[served["delivery_rule"].eq("jour du profil"), "profile_day"]).all()
         and all(d.weekday() in plans[(s, p)].profile
                 for d, s, p in served[["profile_day", "supplier_id", "plant"]].itertuples(index=False))
         and (alloc.loc[alloc["delivery_rule"].eq("démarrage"), "delivery_date"] < start + pd.Timedelta(days=7)).all()),
        ("RG06", "Chaque besoin part avec le dernier départ de son couple à sa date ou avant : un jour de "
                 "livraison couvre les besoins jusqu'au jour de livraison suivant, exclu",
         rg06_coverage(allocations))
        if config.strict_coverage else
        ("RG06 (assouplie)", f"Chaque besoin part avec un départ prévu de son couple compris dans sa fenêtre "
                             f"[date − {config.max_advance_days} j ; date] ; un départ de démarrage ne porte que des "
                             f"besoins datés avant le premier jour du profil",
         bool(((alloc["need_date"] - alloc["delivery_date"]).dt.days.between(0, config.max_advance_days)).all())),
    ]
    if candidates is not None:
        if config.lowest_profile_first:
            rows.append(("RG07", "Aucun profil plus bas n'aurait évité les débordements aussi bien",
                         all(not any(p.frequency < plans[k].frequency and p.overflow_departures <= plans[k].overflow_departures
                                     for p in candidates[k]) for k in plans)))
        elif config.overflow_first:
            rows.append(("RG07", "Aucun autre plan du couple n'a moins de départs dépassant un camion "
                                 "(le client préfère ajouter des jours plutôt que des camions)",
                         all(plans[k].overflow_departures == min(p.overflow_departures for p in candidates[k])
                             for k in plans)))
        else:
            cheapest = sum(min(p.cost for p in candidates[k]) for k in plans)
            budget = config.smoothing_budget if config.smoothing else 0.0
            rows.append(("RG07 - coût", "Le coût total ne dépasse pas la somme des plans les moins chers de chaque "
                                        "couple de plus que le budget de lissage",
                         sum(p.cost for p in plans.values()) <= cheapest * (1 + budget) + 1e-6))
    rows += [
        ("RG08 - unicité", "Chaque ligne du dataset est livrée une et une seule fois",
         alloc["line_id"].is_unique and set(alloc["line_id"]) == set(lines["line_id"])),
        ("RG08 - quantités", "Quantité livrée = quantité demandée, pour chaque usine et chaque material",
         np.allclose(delivered.reindex(demand.index, fill_value=0), demand)),
        ("RG10", "Aucune livraison un jour férié ou un week-end ; un jour de profil férié est livré le jour ouvré précédent",
         all(is_working_day(d.date(), holidays) for d in trucks["date"])
         and all(pd.Timestamp(s).date() in holidays and d < s for s, d in shifted[["profile_day", "delivery_date"]].itertuples(index=False))),
        ("Démarrage", "Aucune livraison avant le premier jour du plan ; un départ de démarrage ne porte que des besoins "
                      "datés avant le premier jour du profil (aucun jour de profil dans leur fenêtre)",
         (trucks["date"] >= start).all()
         and all(cast(pd.Timestamp, row.need_date) < first_profile_day(plans[(str(row.supplier_id), str(row.plant))].profile, start, holidays)
                 for row in alloc[alloc["delivery_rule"].eq("démarrage")].itertuples(index=False))),
        ("RG11 - totaux", "Poids, volume et nombre de lignes de chaque camion = somme de ses lignes",
         np.allclose(t.loc[per_truck.index, "weight"], per_truck["kg"])
         and np.allclose(t.loc[per_truck.index, "volume"], per_truck["m3"])
         and (t.loc[per_truck.index, "lines"] == per_truck["lines"]).all()),
        ("RG11 - coût", "Coût de chaque camion = grille(distance, poids) × (1 + marge) × diesel",
         np.allclose(trucks["cost"], tariff(trucks["distance"], trucks["weight"]))),
    ]
    return pd.DataFrame([(name, what, bool(ok)) for name, what, ok in rows],
                        columns=["contrôle", "ce qui est vérifié", "respecté"])
