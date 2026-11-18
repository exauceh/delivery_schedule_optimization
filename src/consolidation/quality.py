"""Contrôles de qualité des trois sources.

`run_quality_checks` produit le registre des anomalies : une ligne par contrôle,
avec le nombre de lignes concernées, la gravité et la règle de traitement
retenue. Le registre est exporté avec le dataset pour rester traçable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .loaders import RateSheet
from .rates import transport_price

# Gravité : OK (rien à signaler) < INFO < ATTENTION < CRITIQUE
OK, INFO, WARN, CRIT = "OK", "INFO", "ATTENTION", "CRITIQUE"


def _row(check_id, source, check, n, total, severity, rule, detail=""):
    return {
        "id": check_id,
        "source": source,
        "controle": check,
        "n_concernes": int(n),
        "pct": round(100 * n / total, 2) if total else np.nan,
        "gravite": OK if n == 0 else severity,
        "detail": detail,
        "regle_de_traitement": rule if n else "",
    }


def unit_weight_ratio(needs: pd.DataFrame) -> pd.Series:
    """Poids unitaire de chaque ligne rapporté à la médiane de son material."""
    wu = needs["weight"] / needs["schedule_quantity"]
    return wu / wu.groupby(needs["material"]).transform("median")


def schedule_checks(s: pd.DataFrame) -> list[dict]:
    n = len(s)
    needs = s[s["schedule_quantity"] > 0]
    m = len(needs)
    rows = []

    rows.append(_row("S01", "schedule", "Doublons sur la clé (date, fournisseur, material, usine, quai)",
                     s.duplicated(config.LINE_KEY).sum(), n, CRIT, "Agréger les doublons"))
    rows.append(_row("S02", "schedule", "Lignes sans besoin (quantité = 0)",
                     (s["schedule_quantity"] == 0).sum(), n, INFO,
                     "Écartées : rien à livrer (filtre quantité > 0)"))
    rows.append(_row("S03", "schedule", "Valeurs négatives (quantité, poids, volume)",
                     (s[["schedule_quantity", "weight", "volume"]] < 0).any(axis=1).sum(), n, CRIT,
                     "Ligne rejetée"))
    rows.append(_row("S04", "schedule", "Poids manquant sur une ligne avec besoin",
                     needs["weight"].isna().sum(), m, WARN,
                     "Imputation : poids unitaire médian du material x quantité, "
                     "sinon poids médian d'une ligne du couple, sinon médiane globale ; tracé dans `weight_source`"))
    rows.append(_row("S05", "schedule", "Volume manquant sur une ligne avec besoin",
                     needs["volume"].isna().sum(), m, WARN,
                     "Imputation : volume du material (constant par material), "
                     "sinon médiane du couple, sinon médiane globale ; tracé dans `volume_source`"))
    rows.append(_row("S06", "schedule", "Quantité > 0 mais poids = 0",
                     (needs["weight"] == 0).sum(), m, CRIT, "Traité comme poids manquant"))
    rows.append(_row("S07", "schedule", "Quantité = 0 mais volume > 0",
                     ((s["schedule_quantity"] == 0) & (s["volume"] > 0)).sum(), n, INFO,
                     "Sans effet (lignes écartées) ; indique que `volume` est un attribut du material",
                     "Le volume est renseigné même sans besoin"))

    nq = needs.groupby("material")["schedule_quantity"].nunique()
    nv = s.dropna(subset=["volume"]).groupby("material")["volume"].nunique()
    varying = nq[nq > 1].index
    const_vol = int((nv.reindex(varying) == 1).sum())
    rows.append(_row("S08", "schedule", "Volume identique quelle que soit la quantité (materials à quantité variable)",
                     const_vol, len(varying), WARN,
                     "Hypothèse à valider : `volume` utilisé tel quel comme volume de la ligne "
                     "(convention du cahier des charges)",
                     "Valeurs typiques d'emballage (1,2 m³ = palette) : le volume semble être celui d'une unité de manutention"))

    rows.append(_row("S09", "schedule", "Besoin un samedi ou un dimanche",
                     (~s["date"].dt.day_of_week.isin(config.WORKING_WEEKDAYS)).sum(), n, WARN,
                     "Ramener au vendredi précédent"))
    hol = s["date"].dt.date.isin(config.PUBLIC_HOLIDAYS) & (s["schedule_quantity"] > 0)
    rows.append(_row("S10", "schedule", "Besoin un jour férié",
                     hol.sum(), m, INFO,
                     "Conservé et signalé (`is_holiday`) ; le moteur évitera de livrer ces jours-là",
                     ", ".join(sorted(s.loc[hol, "date"].dt.strftime("%d/%m/%Y").unique()))))

    by_day = needs.groupby("date")["weight"].agg(["size", "sum"])
    last = by_day.index.max()
    ratio = by_day.loc[last, "sum"] / by_day["sum"].drop(last).median()
    rows.append(_row("S11", "schedule", "Dernière date = cumul de fin d'horizon",
                     int(by_day.loc[last, "size"]), m, WARN,
                     "Conservé et signalé (`is_horizon_bucket`) ; exclu par défaut de la consolidation hebdomadaire",
                     f"{last:%d/%m/%Y} : {ratio:.1f}x le poids d'un jour médian"))

    rows.append(_row("S12", "schedule", "Ligne seule plus lourde qu'un camion (24,5 t)",
                     (needs["weight"] > config.TRUCK_MAX_WEIGHT_KG).sum(), m, WARN,
                     "Conservée ; nécessitera plusieurs camions (`line_exceeds_truck`)"))
    day_w = needs.groupby(config.COUPLE_KEY + ["date"])["weight"].sum()
    rows.append(_row("S13", "schedule", "Couple x jour plus lourd qu'un camion",
                     (day_w > config.TRUCK_MAX_WEIGHT_KG).sum(), len(day_w), INFO,
                     "Normal : plusieurs camions ce jour-là"))

    ratio_w = unit_weight_ratio(needs)
    f = config.UNIT_WEIGHT_OUTLIER_FACTOR
    out = (ratio_w > f) | (ratio_w < 1 / f)
    rows.append(_row("S14", "schedule", f"Poids unitaire à plus de {f:g}x la médiane du material",
                     out.sum(), m, INFO,
                     "Signalé (`unit_weight_outlier`), non corrigé : le poids est brut, emballage compris",
                     f"{needs.loc[out, 'material'].nunique()} materials concernés"))

    rows.append(_row("S15", "schedule", "Plusieurs versions du programme",
                     max(s["version_date"].nunique() - 1, 0), 1, CRIT,
                     "Garder la version la plus récente"))
    late = (s["transmission_date"] > s["version_date"]).sum()
    rows.append(_row("S16", "schedule", "Date de transmission postérieure à la version",
                     late, n, WARN, "Ignorer `transmission_date` (non utilisée)"))
    past = (needs["date"] <= needs["version_date"]).sum()
    rows.append(_row("S17", "schedule", "Besoin daté avant ou le jour de la version du programme",
                     past, m, WARN, "Livrer au plus tôt"))
    ulp = (s.groupby("unloading_point")["plant"].nunique() > 1).sum()
    rows.append(_row("S18", "schedule", "Quai rattaché à plusieurs usines",
                     ulp, s["unloading_point"].nunique(), CRIT, "Corriger le référentiel"))
    all_c = s[config.COUPLE_KEY].drop_duplicates()
    act_c = needs[config.COUPLE_KEY].drop_duplicates()
    inactive = all_c.merge(act_c, how="left", indicator=True).query("_merge == 'left_only'")
    rows.append(_row("S19", "schedule", "Couple fournisseur x usine sans aucun besoin",
                     len(inactive), len(all_c), INFO, "Exclu de la consolidation (rien à livrer)",
                     ", ".join(inactive["supplier_id"] + "/" + inactive["plant"])))
    return rows


def distance_checks(d: pd.DataFrame, s: pd.DataFrame, rates: RateSheet) -> list[dict]:
    n = len(d)
    rows = []
    rows.append(_row("D01", "distances", "Fournisseur en double",
                     d["supplier_id"].duplicated().sum(), n, CRIT, "Garder une seule distance"))
    rows.append(_row("D02", "distances", "Distance manquante (tous fournisseurs)",
                     d["distance"].isna().sum(), n, INFO, "Sans effet si absent du planning"))
    sched_sup = pd.Index(s["supplier_id"].unique())
    known = d.dropna(subset=["distance"])["supplier_id"]
    miss = sched_sup.difference(pd.Index(known))
    rows.append(_row("D03", "distances", "Fournisseur du planning sans distance",
                     len(miss), len(sched_sup), WARN,
                     f"Imputation par la {config.DISTANCE_IMPUTATION} des distances des fournisseurs du planning "
                     "(`distance_source`)", ", ".join(miss)))
    km_max = float(max(b for b in rates.grid.index if np.isfinite(b)))
    bad = ((d["distance"] <= 0) | (d["distance"] > km_max)).sum()
    rows.append(_row("D04", "distances", f"Distance nulle ou au-delà de la dernière tranche fermée ({km_max:g} km)",
                     bad, n, WARN, "Vérifier la valeur"))
    return rows


def rate_sheet_checks(rates: RateSheet, s: pd.DataFrame) -> list[dict]:
    g = rates.grid
    rows = []
    rows.append(_row("R01", "rate_sheet", "Cellules vides dans la grille",
                     int(g.isna().sum().sum()), g.size, CRIT, "Grille inutilisable",
                     f"{g.shape[0]} tranches km x {g.shape[1]} tranches kg"))
    non_mono = int((g.diff(axis=0).iloc[1:] < 0).sum().sum() + (g.diff(axis=1).iloc[:, 1:] < 0).sum().sum())
    rows.append(_row("R02", "rate_sheet", "Tarif non croissant avec la distance ou le poids",
                     non_mono, g.size, WARN, "Vérifier la cellule"))
    price = transport_price(rates, 300, 1000)
    rows.append(_row("R03", "rate_sheet", "Exemple du PDF (300 km, 1 000 kg = 335,34 EUR) non retrouvé",
                     int(abs(price - 335.34) > 0.01), 1, CRIT, "Revoir l'analyse du PDF",
                     f"Recalculé : {price:.2f} EUR"))
    first_need = s["date"].min().date()
    rows.append(_row("R04", "rate_sheet", "Tarif non valide à la première date du planning",
                     int(rates.valid_from is None or rates.valid_from > first_need), 1, WARN,
                     "Confirmer la grille applicable", f"Valide depuis {rates.valid_from}"))
    heavy = (s.groupby(config.COUPLE_KEY + ["date"])["weight"].sum() > 4000).sum()
    rows.append(_row("R05", "rate_sheet", "Envoi > 4 000 kg : tarif forfaitaire de la tranche ouverte",
                     heavy, s.groupby(config.COUPLE_KEY + ["date"]).ngroups, INFO,
                     "Hypothèse à confirmer : tarif « >4000 » appliqué par envoi (par camion)",
                     "Couple x jour dépassant 4 000 kg dans le planning actuel"))
    return rows


def run_quality_checks(schedule: pd.DataFrame, distances: pd.DataFrame, rates: RateSheet) -> pd.DataFrame:
    rows = schedule_checks(schedule) + distance_checks(distances, schedule, rates) + rate_sheet_checks(rates, schedule)
    return pd.DataFrame(rows)


CARDINALITIES = [
    ("Fournisseur → usines", "supplier_id", "plant"),
    ("Usine → fournisseurs", "plant", "supplier_id"),
    ("Usine → quais", "plant", "unloading_point"),
    ("Quai → usines", "unloading_point", "plant"),
    ("Couple → materials", config.COUPLE_KEY, "material"),
    ("Couple → quais", config.COUPLE_KEY, "unloading_point"),
    ("Material → fournisseurs", "material", "supplier_id"),
    ("Material → usines", "material", "plant"),
    ("Material × usine → fournisseurs", ["material", "plant"], "supplier_id"),
    ("Material × usine → quais", ["material", "plant"], "unloading_point"),
]


def cardinalities(schedule: pd.DataFrame) -> pd.DataFrame:
    """Cardinalités observées entre les entités du planning (lignes avec besoin)."""
    needs = schedule[schedule["schedule_quantity"] > 0]
    rows = []
    for label, left, right in CARDINALITIES:
        g = needs.groupby(left)[right].nunique()
        rows.append({"relation": label, "n_gauche": len(g), "lies_a_plusieurs": int((g > 1).sum()),
                     "moyenne": round(g.mean(), 2), "max": int(g.max()),
                     "type": "1:1" if g.max() == 1 else "1:N"})
    return pd.DataFrame(rows).set_index("relation")


def referential_integrity(schedule: pd.DataFrame, distances: pd.DataFrame, rates: RateSheet) -> pd.DataFrame:
    """Couverture des jointures entre les trois sources."""
    sched_sup = set(schedule["supplier_id"])
    dist_sup = set(distances["supplier_id"])
    known = set(distances.dropna(subset=["distance"])["supplier_id"])
    needs = schedule[schedule["schedule_quantity"] > 0]
    day_w = needs.groupby(config.COUPLE_KEY + ["date"])["weight"].sum()
    d_sched = distances[distances["supplier_id"].isin(sched_sup)]["distance"].dropna()
    km_max = max(b for b in rates.grid.index if np.isfinite(b))
    kg_max = max(b for b in rates.grid.columns if np.isfinite(b))
    rows = [
        ("schedule → distances", "Fournisseurs du planning présents dans distances",
         len(sched_sup & dist_sup), len(sched_sup)),
        ("schedule → distances", "… avec une distance renseignée", len(sched_sup & known), len(sched_sup)),
        ("distances → schedule", "Fournisseurs de distances utilisés par le planning",
         len(dist_sup & sched_sup), len(dist_sup)),
        ("distances → rate_sheet", f"Distances du planning dans les tranches fermées (≤ {km_max:g} km)",
         int((d_sched <= km_max).sum()), len(d_sched)),
        ("schedule → rate_sheet", f"Livraisons actuelles (couple × jour) ≤ {kg_max:g} kg",
         int((day_w <= kg_max).sum()), len(day_w)),
    ]
    out = pd.DataFrame(rows, columns=["jointure", "controle", "couverts", "total"])
    out["couverture_pct"] = (100 * out["couverts"] / out["total"]).round(1)
    return out


def missing_values(df: pd.DataFrame) -> pd.DataFrame:
    """Valeurs manquantes par colonne."""
    na = df.isna().sum()
    return pd.DataFrame({"manquants": na, "pct": (100 * na / len(df)).round(2)}).query("manquants > 0")
