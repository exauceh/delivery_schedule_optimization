"""Outputs 1, 2 and 3 in the layout of the project brief (`courses/consolidation_algorithme_pdf.pdf`, slides 10 to 12).

French headers, the columns of the brief first and in its order, then the detail columns. Used by the Excel report.
Output 1 has exactly the columns of the brief, in the CSV file as in the report; the other CSV files keep the column
names of the milestone 3 notebook (the regression test compares them byte for byte).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

import pandas as pd

from ..io.schemas import PROFILE_COLUMNS, OutputName, profiles_by_code

logger = logging.getLogger(__name__)


def _days(profile_days: str) -> str:
    """'lundi + jeudi' -> 'Lundi, Jeudi' (the "Jour" column of output 1)."""
    return ", ".join(day.strip().capitalize() for day in profile_days.split("+"))


def profiles_sheet(profiles: pd.DataFrame) -> pd.DataFrame:
    """Output 1 (slide 10): the profile to apply for each couple, with the columns of the CSV file (`PROFILE_COLUMNS`)."""
    p = profiles_by_code(profiles)
    sheet = pd.DataFrame({
        "supplier_id": p["supplier_id"],
        "plant": p["plant"],
        "distance": p["distance"].round(1),
        "current_profile": p["current_profile"],
        "profile": p["profile"].map(lambda n: f"{n} / semaine"),
        "profile_days": p["profile_days"].map(_days),
        "trucks_per_active_week": p["trucks_per_active_week"].round(1),
        "mean_fill_weight_pct": p["mean_fill_weight_pct"].round(1),
    })
    return sheet.rename(columns=PROFILE_COLUMNS)


def truck_contents_sheet(contents: pd.DataFrame) -> pd.DataFrame:
    """Output 2 (slide 11): the materials grouped in each truck, with the need days they gather."""
    return pd.DataFrame({
        "Camion": contents["truck_id"],
        "Date de livraison": pd.to_datetime(contents["delivery_date"]).dt.date,
        "Fournisseur": contents["supplier_id"],
        "Usine": contents["plant"],
        "Material": contents["material"],
        "Quai": contents["unloading_point"],
        "Besoins regroupés (jours)": contents["need_days"],
        "Lignes de besoin": contents["need_lines"],
        "Quantité": contents["quantity"],
        "Poids (kg)": contents["weight"].round(1),
        "Volume (m³)": contents["volume"].round(3),
    })


def trucks_sheet(trucks: pd.DataFrame, allocations: pd.DataFrame) -> pd.DataFrame:
    """Output 2, one row per truck (the tiles of slide 11): materials, load, day, current deliveries it replaces.

    A current delivery is one couple x need day, so a truck replaces as many deliveries as it gathers need days.
    """
    per_truck = allocations.groupby("truck_id").agg(materials=("material", "nunique"), replaced=("need_date", "nunique"))
    merged = trucks.join(per_truck, on="truck_id")
    return pd.DataFrame({
        "Camion": merged["truck_id"],
        "Date de livraison": pd.to_datetime(merged["date"]).dt.date,
        "Jour": merged["weekday"].str.capitalize(),
        "Fournisseur": merged["supplier_id"],
        "Usine": merged["plant"],
        "Distance (km)": merged["distance"].round(1),
        "Materials": merged["materials"].fillna(0).astype(int),
        "Lignes de besoin": merged["lines"],
        "Livraisons remplacées": merged["replaced"].fillna(0).astype(int),
        "Poids (kg)": merged["weight"].round(1),
        "Volume (m³)": merged["volume"].round(3),
        "Remplissage poids (%)": (100 * merged["fill_weight"]).round(1),
        "Remplissage volume (%)": (100 * merged["fill_volume"]).round(1),
        "Camion plein imputé": merged["imputed_full_truck"].map({True: "oui", False: "non"}),
        "Coût (EUR)": merged["cost"].round(2),
    })


def saving_sheet(saving: pd.DataFrame) -> pd.DataFrame:
    """Output 3 (slide 12): the potential saving per couple, over the whole planning."""
    return pd.DataFrame({
        "Fournisseur": saving["supplier_id"],
        "Usine": saving["plant"],
        "Distance (km)": saving["distance"].round(1),
        "Coût actuel (EUR)": saving["cost_before"].round(2),
        "Coût après consolidation (EUR)": saving["cost_after"].round(2),
        "Saving (EUR)": saving["saving"].round(2),
        "Saving (%)": saving["saving_pct"].round(1),
        "Livraisons actuelles": saving["deliveries_before"],
        "Livraisons après": saving["deliveries_after"],
        "Camions actuels": saving["trucks_before"],
        "Camions après": saving["trucks_after"],
        "Lignes imputées camion plein": saving["imputed_full_truck_lines"],
    })


def brief_tables(tables: Mapping[OutputName, pd.DataFrame]) -> dict[OutputName, pd.DataFrame]:
    """Outputs 1, 2 and 3 of `tables` in the layout of the brief (those whose source tables are present)."""
    sheets: dict[OutputName, pd.DataFrame] = {}
    if OutputName.PROFILES in tables:
        sheets[OutputName.PROFILES] = profiles_sheet(tables[OutputName.PROFILES])
    if OutputName.TRUCK_CONTENTS in tables:
        sheets[OutputName.TRUCK_CONTENTS] = truck_contents_sheet(tables[OutputName.TRUCK_CONTENTS])
    if OutputName.TRUCKS in tables and OutputName.ALLOCATIONS in tables:
        sheets[OutputName.TRUCKS] = trucks_sheet(tables[OutputName.TRUCKS], tables[OutputName.ALLOCATIONS])
    if OutputName.SAVING in tables:
        sheets[OutputName.SAVING] = saving_sheet(tables[OutputName.SAVING])
    logger.debug("outputs in the layout of the brief: %s", ", ".join(sheets))
    return sheets
