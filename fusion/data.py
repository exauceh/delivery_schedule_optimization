"""Data preparation (rules 1-9): needs, distances, rate sheet and delivery calendar."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import holidays
import numpy as np
import pandas as pd

from .config import Config


@dataclass
class RateSheet:
    km_bounds: np.ndarray     # inclusive upper bounds of the distance rows
    kg_bounds: np.ndarray     # inclusive upper bounds of the weight columns (last = inf)
    grid: np.ndarray          # base prices
    factor: float             # (1 + margin) x diesel

    def row_of(self, km: float) -> int:
        return int(np.searchsorted(self.km_bounds, km, side="left"))

    def price(self, row: int, kg: float) -> float:
        """Price of one truck: grid cell read as 'up to', times margin and diesel (rules 18-21)."""
        return float(self.grid[row, int(np.searchsorted(self.kg_bounds, kg - 1e-9, side="left"))] * self.factor)


@dataclass
class Calendar:
    open_days: list[dt.date]          # delivery days (rules 7-9)
    full_weeks: list[tuple[int, int]]  # ISO weeks entirely inside the delivery window (rule 24)
    holidays: set[dt.date]

    @staticmethod
    def week_key(d: dt.date) -> tuple[int, int]:
        y, w, _ = d.isocalendar()
        return (y, w)


def load_rate_sheet(cfg: Config) -> RateSheet:
    g = pd.read_csv(cfg.rate_sheet_csv, dtype={"km_up_to": str}).set_index("km_up_to").astype(float)
    bound = lambda s: np.inf if str(s).startswith(">") else float(s)
    rs = RateSheet(np.array([bound(k) for k in g.index]), np.array([bound(k) for k in g.columns]), g.to_numpy(),
                   (1 + cfg.margin) * cfg.diesel_surcharge)
    if not (np.diff(rs.grid, axis=1) >= 0).all():
        raise ValueError("rate sheet: price decreases with weight")
    if abs(rs.price(rs.row_of(300), 1000) - 335.34) > 0.01:
        raise ValueError("rate sheet: example 300 km / 1 000 kg != 335.34 EUR")
    return rs


def load_needs(cfg: Config) -> tuple[pd.DataFrame, dict]:
    if cfg.consolidated_csv is not None:                        # milestone 1 already applied rules 1-6
        d = pd.read_csv(cfg.consolidated_csv)
        d["date"] = pd.to_datetime(d["date"]).dt.date
        if d[["weight", "volume", "distance"]].isna().any().any():
            raise ValueError("milestone 1 output still has missing values")
        if ((d["weight"] > cfg.truck_kg + 1e-9) | (d["volume"] > cfg.truck_m3 + 1e-9)).any():
            raise ValueError("a line is larger than a truck")
        d["load_missing"] = (d["weight"] >= cfg.truck_kg - 1e-6) | (d["volume"] >= cfg.truck_m3 - 1e-6)
        d = d.reset_index(drop=True)
        d["line_id"] = d.index
        log = {"source": f"{cfg.consolidated_csv} (Milestone 1)", "lignes_retenues": len(d),
               "lignes_camion_plein_imputees": int(d["load_missing"].sum()),
               "horizon": f"{d['date'].min()} -> {d['date'].max()}"}
        return d, log
    raw = pd.read_csv(cfg.schedule_csv, encoding="utf-8-sig")
    raw["date"] = pd.to_datetime(raw["date"]).dt.date
    log = {"lignes_brutes": len(raw)}
    cumul = {dt.date.fromisoformat(s) for s in cfg.cumulative_dates}
    d = raw[~raw["date"].isin(cumul)]                                           # rule 2
    log["exclues_cumul"] = len(raw) - len(d)
    n = len(d); d = d[d["schedule_quantity"] > 0]                               # rule 3
    log["exclues_quantite_0"] = n - len(d)
    if cfg.drop_lines_over_capacity:                                            # rule 4
        n = len(d); d = d[~((d["weight"] > cfg.truck_kg) | (d["volume"] > cfg.truck_m3))]
        log["exclues_plus_grandes_qu_un_camion"] = n - len(d)
    d = d.copy()
    d["load_missing"] = d["weight"].isna() | d["volume"].isna() | (d["weight"] <= 0) | (d["volume"] <= 0)
    log["poids_ou_volume_manquant"] = int(d["load_missing"].sum())
    for col, cap in (("weight", cfg.truck_kg), ("volume", cfg.truck_m3)):     # rule 5
        miss = d[col].isna() | (d[col] <= 0)
        if cfg.missing_load == "full_truck":
            d.loc[miss, col] = cap
        else:
            known = d[col].where(~miss)
            d.loc[miss, col] = (known.groupby([d["supplier_id"], d["plant"]]).transform("median")[miss]
                                .fillna(known.groupby(d["plant"]).transform("median")[miss]))
    dist = pd.read_csv(cfg.distances_csv).drop_duplicates("supplier_id").set_index("supplier_id")["distance"]
    known = dist[dist > 0]
    fill = known.max() if cfg.missing_distance == "max" else known.median()
    d["distance"] = d["supplier_id"].map(known)
    log["distance_manquante"] = ", ".join(sorted(d.loc[d["distance"].isna(), "supplier_id"].unique())) + f" -> {fill:g} km"
    d["distance_missing"] = d["distance"].isna()
    d["distance"] = d["distance"].fillna(fill)                                  # rule 6
    if ((d["weight"] > cfg.truck_kg + 1e-9) | (d["volume"] > cfg.truck_m3 + 1e-9)).any():
        raise ValueError("a line is larger than a truck (rule 4 disabled?)")
    d = d.reset_index(drop=True)
    d["line_id"] = d.index
    log["lignes_retenues"] = len(d)
    log["horizon"] = f"{d['date'].min()} -> {d['date'].max()}"                 # rule 1, read from the data
    return d, log


def build_calendar(cfg: Config, needs: pd.DataFrame) -> Calendar:
    first_need, last_need = needs["date"].min(), needs["date"].max()
    start = first_need - dt.timedelta(days=cfg.anticipation_days) if cfg.deliver_before_first_need else first_need
    hol = set(holidays.country_holidays(cfg.holiday_country, years=range(start.year, last_need.year + 1)).keys())
    days = [start + dt.timedelta(days=i) for i in range((last_need - start).days + 1)]
    open_days = [d for d in days if d.weekday() < 5 and d not in hol]
    full = sorted({Calendar.week_key(d) for d in open_days
                   if d - dt.timedelta(days=d.weekday()) >= start
                   and d - dt.timedelta(days=d.weekday()) + dt.timedelta(days=4) <= last_need})
    if not set(needs["date"]) <= set(open_days):
        raise ValueError("a need falls on a day without delivery")
    return Calendar(open_days, full, hol)
