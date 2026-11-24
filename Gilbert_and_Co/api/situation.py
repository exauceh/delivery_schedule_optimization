"""Situation actuelle d'un import : contrôle des données, registre des anomalies, coût sans regroupement.

Tout passe par la préparation du moteur (fusion.data.load_needs) : ce qui est affiché ici est exactement ce que
l'optimisation recevra. Le coût actuel suit la Milestone 2 : chaque ligne livrée le jour de son besoin, les lignes
d'un même fournisseur pour une même usine et un même jour partagent les camions.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path

import holidays
import numpy as np
import pandas as pd

from fusion.config import Config
from fusion.data import load_needs, load_rate_sheet
from fusion.loading import Loader

TREATMENTS = {
    "weight": ("full_truck", "max", "median", "drop"),
    "volume": ("full_truck", "max", "median", "drop"),
    "distance": ("max", "median", "drop"),
}
LABELS = {
    "full_truck": "la capacité d'un camion",
    "max": "le maximum connu",
    "median": "la médiane connue",
    "drop": "lignes retirées",
}
REQUIRED = {
    "schedule": {"date", "supplier_id", "material", "plant", "schedule_quantity", "weight", "volume"},
    "distances": {"supplier_id", "distance"},
}


def check_columns(kind: str, path: Path) -> None:
    cols = set(pd.read_csv(path, nrows=0, encoding="utf-8-sig").columns)
    missing = REQUIRED[kind] - cols
    if missing:
        raise ValueError(f"colonnes manquantes : {', '.join(sorted(missing))}")


def describe(schedule: Path) -> dict:
    """Period and size of a planning file, read once at import time (for the history and its timeline)."""
    cfg = Config()
    raw = pd.read_csv(schedule, encoding="utf-8-sig")
    raw["date"] = pd.to_datetime(raw["date"]).dt.date
    cumul = {dt.date.fromisoformat(s) for s in cfg.cumulative_dates}
    need = raw[(~raw["date"].isin(cumul)) & (raw["schedule_quantity"] > 0)]
    return {"lines": int(len(raw)), "need_lines": int(len(need)),
            "first": need["date"].min().isoformat() if len(need) else None,
            "last": need["date"].max().isoformat() if len(need) else None,
            "suppliers": int(need["supplier_id"].nunique()), "plants": sorted(need["plant"].unique().tolist())}


def _fmt(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def _band(bounds: np.ndarray, unit: str) -> list[str]:
    out, low = [], 0
    for b in bounds:
        out.append(f"> {_fmt(low)} {unit}" if np.isinf(b) else f"≤ {_fmt(b)} {unit}")
        low = b
    return out


@lru_cache(maxsize=64)
def analyse(folder: str, weight: str = "full_truck", volume: str = "full_truck", distance: str = "max") -> dict:
    d = Path(folder)
    files = {"schedule": d / "released_schedule.csv", "distances": d / "distances.csv", "rates": d / "rate_sheet.csv"}
    cfg = Config(consolidated_csv=None, schedule_csv=files["schedule"], distances_csv=files["distances"],
                 rate_sheet_csv=files["rates"], missing_weight=weight, missing_volume=volume, missing_distance=distance)

    # ---------- registre des anomalies, lu sur les fichiers bruts
    raw = pd.read_csv(files["schedule"], encoding="utf-8-sig")
    raw["date"] = pd.to_datetime(raw["date"]).dt.date
    dist = pd.read_csv(files["distances"])
    cumul = {dt.date.fromisoformat(s) for s in cfg.cumulative_dates}
    is_cumul = raw["date"].isin(cumul)
    zero = ~is_cumul & ~(raw["schedule_quantity"] > 0)
    kept = raw[~is_cumul & ~zero]
    too_big = (kept["weight"] > cfg.truck_kg) | (kept["volume"] > cfg.truck_m3)
    kept = kept[~too_big]
    w_miss = kept["weight"].isna() | (kept["weight"] <= 0)
    v_miss = kept["volume"].isna() | (kept["volume"] <= 0)
    known_dist = dist.drop_duplicates("supplier_id").set_index("supplier_id")["distance"]
    known_dist = known_dist[known_dist > 0]
    no_dist = sorted(set(kept["supplier_id"]) - set(known_dist.index))
    dist_lines = int(kept["supplier_id"].isin(no_dist).sum())
    dup = int(raw.duplicated(["date", "supplier_id", "material", "plant"] + (["unloading_point"] if "unloading_point" in raw else [])).sum())
    dist_file_missing = int((dist["distance"].isna() | (dist["distance"] <= 0)).sum())
    hol = set(holidays.country_holidays(cfg.holiday_country, years=range(2020, 2031)).keys())
    closed = kept[[x.weekday() >= 5 or x in hol for x in kept["date"]]]

    def ex(values, n=6):
        values = list(values)
        return ", ".join(map(str, values[:n])) + (f" … (+{len(values) - n})" if len(values) > n else "")

    reg = [
        dict(code="cumul", label="Lignes de cumul", count=int(is_cumul.sum()), unit="lignes",
             detail=f"datées du {', '.join(x.strftime('%d/%m/%Y') for x in sorted(cumul))} : un total, pas un besoin",
             treatment="retirées", kind="corrige"),
        dict(code="zero", label="Quantité nulle", count=int(zero.sum()), unit="lignes",
             detail="aucune pièce à livrer ce jour-là", treatment="retirées", kind="corrige"),
        dict(code="too_big", label="Ligne plus grande qu'un camion", count=int(too_big.sum()), unit="lignes",
             detail=f"plus de {_fmt(cfg.truck_kg)} kg ou de {cfg.truck_m3:g} m³ : "
                    + ex(f"{r.supplier_id} le {r.date:%d/%m}" for r in raw.loc[too_big[too_big].index].itertuples()),
             treatment="retirées", kind="corrige"),
        dict(code="weight", label="Poids manquant ou nul", count=int(w_miss.sum()), unit="lignes",
             detail=f"{kept.loc[w_miss, 'supplier_id'].nunique()} fournisseurs concernés",
             treatment="retirées" if weight == "drop" else f"remplacé par {LABELS[weight]}", kind="choix", field="weight"),
        dict(code="volume", label="Volume manquant ou nul", count=int(v_miss.sum()), unit="lignes",
             detail=f"{kept.loc[v_miss, 'supplier_id'].nunique()} fournisseurs concernés",
             treatment="retirées" if volume == "drop" else f"remplacé par {LABELS[volume]}", kind="choix", field="volume"),
        dict(code="distance", label="Distance inconnue", count=dist_lines, unit="lignes",
             detail=" · ".join(x for x in ((f"fournisseurs : {ex(no_dist)}" if no_dist else ""),
                                           (f"{dist_file_missing} fournisseurs sans distance dans le fichier" if dist_file_missing else "")) if x),
             treatment="retirées" if distance == "drop" else f"remplacée par {LABELS[distance]}", kind="choix", field="distance"),
        dict(code="dup", label="Doublons", count=dup, unit="lignes",
             detail="même jour, fournisseur, pièce et usine", treatment="conservés et additionnés dans les camions", kind="info"),
        dict(code="closed", label="Besoin un jour non ouvré", count=int(len(closed)), unit="lignes",
             detail=ex(f"{r.supplier_id} le {r.date:%d/%m}" for r in closed.itertuples()),
             treatment="à corriger dans le fichier : le calcul s'arrêterait", kind="bloquant"),
    ]

    # ---------- préparation du moteur, avec les traitements choisis
    needs, log = load_needs(cfg)
    rates = load_rate_sheet(cfg)
    loader = Loader(cfg, rates)
    km_labels, kg_labels = _band(rates.km_bounds, "km"), _band(rates.kg_bounds, "kg")
    heat = np.zeros((len(rates.km_bounds), len(rates.kg_bounds)), dtype=int)
    heat_cost = np.zeros_like(heat, dtype=float)
    per_day: dict[tuple[str, dt.date], int] = {}
    trucks = deliveries = 0
    cost = fill = 0.0
    plants: dict[str, dict] = {}
    for (sup, plant), g in needs.groupby(["supplier_id", "plant"]):
        row = rates.row_of(g["distance"].iloc[0])
        for day, gg in g.groupby("date"):
            bins = loader.ffd(list(zip(gg.weight, gg.volume, gg.line_id)), row)
            deliveries += 1
            p = plants.setdefault(plant, dict(plant=plant, cost=0.0, trucks=0, deliveries=0, suppliers=set()))
            p["deliveries"] += 1; p["suppliers"].add(sup)
            for w, v, _ in bins:
                col = int(np.searchsorted(rates.kg_bounds, w - 1e-9, side="left"))
                price = rates.price(row, w)
                heat[row, col] += 1; heat_cost[row, col] += price
                cost += price; trucks += 1; fill += max(w / cfg.truck_kg, v / cfg.truck_m3)
                p["cost"] += price; p["trucks"] += 1
                per_day[(plant, day)] = per_day.get((plant, day), 0) + 1

    first, last = needs["date"].min(), needs["date"].max()
    days = [x.date() for x in pd.bdate_range(first, last)]
    plant_names = sorted(plants)
    n_cumul, n_zero, n_big = reg[0]["count"], reg[1]["count"], reg[2]["count"]
    rules = [r for r in (
        f"{n_cumul} lignes de cumul retirées (elles totalisent la période, ce ne sont pas des besoins)." if n_cumul else None,
        f"{n_zero} lignes à quantité nulle retirées." if n_zero else None,
        (f"{n_big} ligne plus grande qu'un camion retirée." if n_big == 1 else f"{n_big} lignes plus grandes qu'un camion retirées.") if n_big else None,
    ) if r] + [
        _rule("Poids manquant", reg[3]["count"], weight, f"{_fmt(cfg.truck_kg)} kg"),
        _rule("Volume manquant", reg[4]["count"], volume, f"{cfg.truck_m3:g} m³"),
        _rule("Distance inconnue", reg[5]["count"], distance, None, log.get("distance_manquante", "")),
        "Chaque ligne est livrée le jour de son besoin ; les lignes d'un fournisseur pour une usine le même jour "
        f"partagent les camions ({_fmt(cfg.truck_kg)} kg et {cfg.truck_m3:g} m³ au plus par camion).",
        f"Prix d'un camion : case de la grille (distance × poids chargé) × (1 + marge {cfg.margin:g}) × gazole {cfg.diesel_surcharge:g}.",
    ]
    return {
        "params": {"weight": weight, "volume": volume, "distance": distance},
        "kpi": {"first": first.isoformat(), "last": last.isoformat(), "business_days": len(days),
                "weeks": len({x.isocalendar()[:2] for x in days}), "raw_lines": int(len(raw)), "lines": int(len(needs)),
                "suppliers": int(needs["supplier_id"].nunique()), "couples": int(needs.groupby(["supplier_id", "plant"]).ngroups),
                "plants": len(plant_names), "deliveries": deliveries, "trucks": trucks, "cost": round(cost, 2),
                "fill_pct": round(100 * fill / trucks, 1) if trucks else 0,
                "load_missing_lines": int(needs["load_missing"].sum())},
        "anomalies": reg,
        "blocking": any(a["kind"] == "bloquant" and a["count"] for a in reg),
        "rules": rules,
        "plants": [dict(plant=p, cost=round(plants[p]["cost"], 2), trucks=plants[p]["trucks"],
                        deliveries=plants[p]["deliveries"], suppliers=len(plants[p]["suppliers"])) for p in plant_names],
        "calendar": {"plants": plant_names, "days": [x.isoformat() for x in days],
                     "trucks": [[per_day.get((p, x), 0) for x in days] for p in plant_names]},
        "heatmap": {"km": km_labels, "kg": kg_labels, "trucks": heat.tolist(), "cost": np.round(heat_cost, 2).tolist(),
                    "price": np.round(rates.grid * rates.factor, 2).tolist()},
    }


def _rule(what: str, n: int, how: str, cap: str | None, extra: str = "") -> str:
    if not n:
        return f"{what} : aucune ligne concernée."
    if how == "drop":
        return f"{what} : {n} lignes retirées du calcul."
    if how == "full_truck":
        return f"{what} : {n} lignes comptées comme un camion plein ({cap})."
    target = "du couple fournisseur × usine, à défaut de l'usine" if what != "Distance inconnue" else "de tous les fournisseurs"
    word = "maximum" if how == "max" else "médiane"
    tail = f" ({extra.split('->')[-1].strip()})" if extra and "->" in extra else ""
    return f"{what} : {n} lignes complétées par la {word} {target}{tail}." if how == "median" else \
           f"{what} : {n} lignes complétées par le {word} {target}{tail}."
