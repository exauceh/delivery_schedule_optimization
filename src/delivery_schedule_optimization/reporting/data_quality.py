"""Data quality of an import, before any optimisation: what the preparation finds and the missing data to complete.

Shown by the interface right after the files are dropped. The preparation takes a fraction of a second, so it runs
twice: once the prudent way (full truck, longest distance) and once with the medians, to show what each choice gives.
"""

from __future__ import annotations

import logging

import pandas as pd
from pydantic import BaseModel

from ..domain.config import COUPLE, PlannerConfig, PreparationConfig
from ..preparation.pipeline import prepare
from .dashboard import Anomaly, anomaly

logger = logging.getLogger(__name__)

TO_CHOOSE = "à compléter : choisissez la méthode dans les réglages"


class LoadSources(BaseModel):
    """How the missing loads (weight or volume) of one column would be completed with the median method."""

    rows: int  # lines without this value
    material: int  # completed by the median per piece of the same material
    supplier: int  # by the median line of the supplier (no known line of the material)
    full_truck: int  # by a full truck (no known line of the supplier either)


class MissingData(BaseModel):
    weight: LoadSources
    volume: LoadSources
    suppliers_without_distance: list[str]
    distance_max: float  # km given by the "max" method
    distance_median: float  # km given by the "median" method

    @property
    def any(self) -> bool:
        return bool(self.weight.rows or self.volume.rows or self.suppliers_without_distance)


class DataQuality(BaseModel):
    rows_raw: int  # rows of the released schedule
    lines: int  # needs kept after the preparation
    suppliers: int
    plants: int
    couples: int
    materials: int
    first_date: str  # first and last need (ISO dates)
    last_date: str
    checks: list[Anomaly]  # findings of the preparation (DQ01 to DQ07), with their treatment
    missing: MissingData
    has_missing: bool  # True: the user chooses how to complete the missing data before launching


def _sources(loads: dict[str, dict[str, int]], column: str) -> LoadSources:
    median = loads.get(column, {})
    return LoadSources(rows=sum(median.values()), material=median.get("material", 0), supplier=median.get("supplier", 0),
                       full_truck=median.get("full_truck", 0))


def data_quality(schedule_raw: pd.DataFrame, distances_raw: pd.DataFrame, planner: PlannerConfig | None = None) -> DataQuality:
    """Quality report of the raw schedule and distances (raises ValueError when nothing can be planned)."""
    planner = planner or PlannerConfig()
    prudent = prepare(schedule_raw, distances_raw, planner, PreparationConfig())
    median = prepare(schedule_raw, distances_raw, planner, PreparationConfig(missing_load="material_median", missing_distance="median"))
    lines = prudent.consolidated
    register = median.register
    missing = MissingData(weight=_sources(register.missing_loads, "weight"), volume=_sources(register.missing_loads, "volume"),
                          suppliers_without_distance=register.missing_distances,
                          distance_max=register.distance_fallbacks.get("max", 0.0), distance_median=register.distance_fallbacks.get("median", 0.0))
    checks = []
    register_frame = prudent.anomalies
    for rule, action, rows in zip(register_frame["rule"].astype(str), register_frame["action"].astype(str), register_frame["rows"].astype(int)):
        found = anomaly(rule, action, int(rows))
        if found.rule in ("DQ04", "DQ07") and found.count:  # the treatment depends on the choice of the user
            found = found.model_copy(update={"treatment": TO_CHOOSE})
        checks.append(found)
    report = DataQuality(
        rows_raw=len(schedule_raw), lines=len(lines), suppliers=int(lines["supplier_id"].nunique()), plants=int(lines["plant"].nunique()),
        couples=len(lines.drop_duplicates(COUPLE)), materials=int(lines["material"].nunique()),
        first_date=pd.Timestamp(lines["date"].min()).date().isoformat(), last_date=pd.Timestamp(lines["date"].max()).date().isoformat(),
        checks=checks, missing=missing, has_missing=missing.any,
    )
    logger.info("Data quality: %d rows -> %d lines, missing weights %d, volumes %d, distances %d", report.rows_raw, report.lines,
                missing.weight.rows, missing.volume.rows, len(missing.suppliers_without_distance))
    return report
