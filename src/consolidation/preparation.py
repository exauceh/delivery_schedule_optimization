"""Pipeline de nettoyage et d'agrégation.

Entrées brutes -> trois tables prêtes pour le moteur de consolidation :

* `lines`      : un besoin (quantité > 0) par ligne, nettoyé, avec distance,
                 calendrier et indicateurs d'imputation / d'anomalie.
                 `line_id` renvoie à la ligne du fichier source (traçabilité).
* `couple_day` : charge par couple fournisseur x usine x jour = la situation
                 « une livraison par jour de besoin ».
* `couples`    : une ligne par couple, avec sa distance et ses ordres de grandeur.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .loaders import RateSheet
from .quality import is_missing, unit_weight_ratio

WEEKDAY_NAMES = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


@dataclass
class PreparationResult:
    lines: pd.DataFrame
    couple_day: pd.DataFrame
    couples: pd.DataFrame
    log: list[dict] = field(default_factory=list)

    @property
    def log_table(self) -> pd.DataFrame:
        return pd.DataFrame(self.log)


# --------------------------------------------------------------------------- #
# Étapes élémentaires (chacune testable seule)
# --------------------------------------------------------------------------- #
def keep_latest_version(schedule: pd.DataFrame) -> pd.DataFrame:
    return schedule[schedule["version_date"] == schedule["version_date"].max()].copy()


def keep_positive_needs(schedule: pd.DataFrame) -> pd.DataFrame:
    return schedule[schedule["schedule_quantity"] > 0].copy()


def _fill_by_hierarchy(target: pd.Series, candidates: list[tuple[str, pd.Series]]) -> tuple[pd.Series, pd.Series]:
    """Remplit `target` avec la première source candidate disponible et renvoie la source utilisée."""
    values = target.copy()
    source = pd.Series(np.where(values.notna(), "source", None), index=values.index, dtype="object")
    for name, cand in candidates:
        fill = values.isna() & cand.notna()
        values[fill] = cand[fill]
        source[fill] = name
    source[values.isna()] = "manquant"
    return values, source


def impute_volume(lines: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """Volume manquant ou nul -> volume du material (constant par material, toutes lignes du
    planning, y compris quantité 0) -> médiane du couple -> médiane globale."""
    out = lines.copy()
    out.loc[is_missing(out["volume"]), "volume"] = np.nan
    ref = reference[~is_missing(reference["volume"])]
    mat_vol = ref.groupby("material")["volume"].agg(lambda x: x.mode().iat[0])
    couple_med = out.groupby(config.COUPLE_KEY)["volume"].transform("median")
    out["volume"], out["volume_source"] = _fill_by_hierarchy(out["volume"], [
        ("material", out["material"].map(mat_vol)),
        ("couple", couple_med),
        ("global", pd.Series(out["volume"].median(), index=out.index)),
    ])
    return out


def impute_weight(lines: pd.DataFrame) -> pd.DataFrame:
    """Poids manquant ou nul -> poids unitaire médian du material x quantité
    -> poids médian d'une ligne du couple -> poids médian global d'une ligne."""
    out = lines.copy()
    out.loc[is_missing(out["weight"]), "weight"] = np.nan
    unit = (out["weight"] / out["schedule_quantity"]).groupby(out["material"]).median()
    couple_med = out.groupby(config.COUPLE_KEY)["weight"].transform("median")
    out["weight"], out["weight_source"] = _fill_by_hierarchy(out["weight"], [
        ("material", out["material"].map(unit) * out["schedule_quantity"]),
        ("couple", couple_med),
        ("global", pd.Series(out["weight"].median(), index=out.index)),
    ])
    return out


def add_distances(lines: pd.DataFrame, distances: pd.DataFrame,
                  strategy: str = config.DISTANCE_IMPUTATION) -> pd.DataFrame:
    d = distances.drop_duplicates("supplier_id").set_index("supplier_id")["distance"]
    out = lines.copy()
    out["distance"] = out["supplier_id"].map(d)
    if strategy != "median":
        raise ValueError(f"Stratégie d'imputation inconnue : {strategy}")
    ref = d.reindex(out["supplier_id"].unique()).median()
    out["distance"], out["distance_source"] = _fill_by_hierarchy(
        out["distance"], [("median", pd.Series(ref, index=out.index))])
    return out


def add_calendar(lines: pd.DataFrame, horizon_bucket: bool = config.TREAT_LAST_DATE_AS_HORIZON_BUCKET,
                 holidays=config.PUBLIC_HOLIDAYS) -> pd.DataFrame:
    out = lines.copy()
    iso = out["date"].dt.isocalendar()
    out["iso_year"], out["iso_week"] = iso["year"].astype(int), iso["week"].astype(int)
    out["week_start"] = out["date"] - pd.to_timedelta(out["date"].dt.day_of_week, unit="D")
    out["weekday"] = out["date"].dt.day_of_week
    out["weekday_name"] = out["weekday"].map(dict(enumerate(WEEKDAY_NAMES)))
    out["is_holiday"] = out["date"].dt.date.isin(holidays)
    out["is_horizon_bucket"] = (out["date"] == out["date"].max()) if horizon_bucket else False
    return out


def flag_anomalies(lines: pd.DataFrame) -> pd.DataFrame:
    out = lines.copy()
    f = config.UNIT_WEIGHT_OUTLIER_FACTOR
    r = unit_weight_ratio(out)
    out["unit_weight_outlier"] = (r > f) | (r < 1 / f)
    out["line_exceeds_truck"] = (out["weight"] > config.TRUCK_MAX_WEIGHT_KG) | (out["volume"] > config.TRUCK_MAX_VOLUME_M3)
    out["is_imputed"] = (out["weight_source"] != "source") | (out["volume_source"] != "source")
    return out


# --------------------------------------------------------------------------- #
# Agrégations
# --------------------------------------------------------------------------- #
def _round_sums(df: pd.DataFrame, cols: list[str], decimals: int = 6) -> pd.DataFrame:
    """Arrondit des sommes de flottants. L'ordre d'addition varie selon la version de
    pandas (écarts ~1e-11) ; arrondir les sommes avant de calculer les ratios rend les
    résultats identiques d'une version à l'autre (poids au 1/100 kg, volumes au 1/10 000 m³
    dans la source : l'arrondi à 6 décimales ne perd rien)."""
    out = df.copy()
    out[cols] = out[cols].round(decimals)
    return out


def aggregate_couple_day(lines: pd.DataFrame) -> pd.DataFrame:
    """Charge d'un couple pour un jour de besoin (= une livraison dans la situation actuelle)."""
    keys = config.COUPLE_KEY + ["date"]
    agg = lines.groupby(keys, observed=True).agg(
        n_lines=("line_id", "size"),
        n_materials=("material", "nunique"),
        n_unloading_points=("unloading_point", "nunique"),
        quantity=("schedule_quantity", "sum"),
        weight=("weight", "sum"),
        volume=("volume", "sum"),
        distance=("distance", "first"),
        n_imputed_lines=("is_imputed", "sum"),
        week_start=("week_start", "first"),
        weekday=("weekday", "first"),
        is_holiday=("is_holiday", "first"),
        is_horizon_bucket=("is_horizon_bucket", "first"),
    ).reset_index()
    agg = _round_sums(agg, ["quantity", "weight", "volume"])
    agg["fill_weight"] = agg["weight"] / config.TRUCK_MAX_WEIGHT_KG
    agg["fill_volume"] = agg["volume"] / config.TRUCK_MAX_VOLUME_M3
    # Un camion est plein dès qu'une des deux limites est atteinte
    agg["min_trucks"] = np.ceil(agg[["fill_weight", "fill_volume"]].max(axis=1)).astype(int)
    return agg


def summarize_couples(lines: pd.DataFrame, couple_day: pd.DataFrame) -> pd.DataFrame:
    firm = couple_day[~couple_day["is_horizon_bucket"]]
    n_weeks = firm["week_start"].nunique()
    lines_s = lines.groupby(config.COUPLE_KEY).agg(
        distance=("distance", "first"),
        distance_source=("distance_source", "first"),
        n_materials=("material", "nunique"),
        n_unloading_points=("unloading_point", "nunique"),
        n_lines=("line_id", "size"),
        total_weight=("weight", "sum"),
        total_volume=("volume", "sum"),
        imputed_weight=("weight", lambda w: w[lines.loc[w.index, "weight_source"] != "source"].sum()),
    )
    days = firm.groupby(config.COUPLE_KEY).agg(
        n_delivery_days=("date", "size"),
        n_active_weeks=("week_start", "nunique"),
        weight_firm=("weight", "sum"),
        median_delivery_weight=("weight", "median"),
        median_fill_weight=("fill_weight", "median"),
    )
    out = _round_sums(lines_s.join(days, how="left").reset_index(),
                      ["total_weight", "total_volume", "imputed_weight", "weight_firm", "median_delivery_weight"])
    out["deliveries_per_week"] = out["n_delivery_days"] / n_weeks
    out["weekly_weight"] = out["weight_firm"] / n_weeks
    out["imputed_weight_share"] = out["imputed_weight"] / out["total_weight"]
    return out.sort_values(["plant", "supplier_id"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Pipeline complet
# --------------------------------------------------------------------------- #
def prepare(schedule: pd.DataFrame, distances: pd.DataFrame) -> PreparationResult:
    log: list[dict] = []

    def step(name: str, df: pd.DataFrame, comment: str = "") -> pd.DataFrame:
        log.append({"etape": name, "lignes": len(df), "commentaire": comment})
        return df

    df = step("Chargement", schedule, "Fichier brut")
    df = step("Dernière version du programme", keep_latest_version(df))
    df = step("Filtre quantité > 0", keep_positive_needs(df), "Lignes sans besoin écartées")
    df = impute_weight(df)
    df = step("Imputation du poids", df, f"{(df['weight_source'] != 'source').sum()} lignes imputées")
    df = impute_volume(df, reference=schedule)
    df = step("Imputation du volume", df, f"{(df['volume_source'] != 'source').sum()} lignes imputées")
    df = add_distances(df, distances)
    df = step("Ajout des distances", df, f"{(df['distance_source'] != 'source').sum()} lignes à distance imputée")
    df = add_calendar(df)
    df = step("Calendrier", df, f"{int(df['is_horizon_bucket'].sum())} lignes dans le cumul de fin d'horizon")
    df = flag_anomalies(df)
    df = step("Signalement des anomalies", df, f"{int(df['unit_weight_outlier'].sum())} poids unitaires atypiques")

    cols = ["line_id", "date", "week_start", "iso_year", "iso_week", "weekday", "weekday_name",
            "supplier_id", "plant", "unloading_point", "material", "schedule_quantity",
            "weight", "weight_source", "volume", "volume_source", "distance", "distance_source",
            "is_holiday", "is_horizon_bucket", "unit_weight_outlier", "line_exceeds_truck", "is_imputed"]
    lines = df[cols].sort_values(config.COUPLE_KEY + ["date", "unloading_point", "material"]).reset_index(drop=True)
    couple_day = aggregate_couple_day(lines)
    log.append({"etape": "Agrégation couple x jour", "lignes": len(couple_day), "commentaire": "Livraisons actuelles"})
    couples = summarize_couples(lines, couple_day)
    log.append({"etape": "Synthèse par couple", "lignes": len(couples), "commentaire": "Couples avec au moins un besoin"})
    return PreparationResult(lines=lines, couple_day=couple_day, couples=couples, log=log)


def validate(result: PreparationResult, schedule: pd.DataFrame) -> pd.DataFrame:
    """Contrôles d'acceptation du dataset consolidé (tous doivent valoir True)."""
    lines, cd = result.lines, result.couple_day
    src = keep_positive_needs(keep_latest_version(schedule))
    checks = {
        "Aucun besoin perdu (une ligne par besoin source)": len(lines) == len(src),
        "Quantité totale conservée": np.isclose(lines["schedule_quantity"].sum(), src["schedule_quantity"].sum()),
        "line_id unique et présent dans la source": lines["line_id"].is_unique and lines["line_id"].isin(schedule["line_id"]).all(),
        "Aucune valeur manquante (poids, volume, distance)": not lines[["weight", "volume", "distance"]].isna().any().any(),
        "Poids et volumes strictement positifs": bool((lines["weight"] > 0).all() and (lines["volume"] > 0).all()),
        "Poids non imputés identiques à la source": np.allclose(
            lines.query("weight_source == 'source'").set_index("line_id")["weight"].sort_index(),
            src.set_index("line_id").loc[lines.query("weight_source == 'source'")["line_id"], "weight"].sort_index()),
        "Uniquement des jours ouvrés": bool(lines["weekday"].isin(config.WORKING_WEEKDAYS).all()),
        "Poids agrégé = somme des lignes": np.isclose(cd["weight"].sum(), lines["weight"].sum()),
        "Une seule usine par couple x jour": bool(lines.groupby(config.COUPLE_KEY + ["date"])["plant"].nunique().eq(1).all()),
    }
    return pd.DataFrame({"controle": list(checks), "ok": [bool(v) for v in checks.values()]})


def save_outputs(result: PreparationResult, rates: RateSheet, quality: pd.DataFrame,
                 out_dir: Path = config.PROCESSED_DIR) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "lines": out_dir / "needs_clean.csv",
        "couple_day": out_dir / "deliveries_couple_day.csv",
        "couples": out_dir / "couples.csv",
        "rate_sheet": out_dir / "rate_sheet.csv",
        "rate_params": out_dir / "rate_params.csv",
        "quality": out_dir / "quality_report.csv",
        "log": out_dir / "pipeline_log.csv",
    }
    result.lines.to_csv(files["lines"], index=False, date_format="%Y-%m-%d")
    result.couple_day.to_csv(files["couple_day"], index=False, date_format="%Y-%m-%d")
    result.couples.to_csv(files["couples"], index=False)
    rates.to_long().to_csv(files["rate_sheet"], index=False)
    pd.DataFrame([{"margin": rates.margin, "diesel_surcharge": rates.diesel_surcharge,
                   "price_factor": rates.price_factor, "valid_from": rates.valid_from}]
                 ).to_csv(files["rate_params"], index=False)
    quality.to_csv(files["quality"], index=False)
    result.log_table.to_csv(files["log"], index=False)
    return files
