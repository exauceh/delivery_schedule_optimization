"""Data quality: register of the anomalies found while preparing the data (EF02) and accuracy of the dataset."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

REGISTER_COLUMNS = ["rule", "source", "severity", "rows", "action"]


@dataclass
class AnomalyRegister:
    """Every finding of the preparation: rule (DQxx), source file, severity, rows concerned and action taken."""

    entries: list[dict[str, object]] = field(default_factory=list)
    # missing data and how they were completed (shown by the data quality report of the interface)
    missing_loads: dict[str, dict[str, int]] = field(default_factory=dict)  # column -> rows per source (material, supplier, full_truck)
    missing_distances: list[str] = field(default_factory=list)  # suppliers of the planning without a distance
    distance_fallbacks: dict[str, float] = field(default_factory=dict)  # "max" and "median" distance of the planning suppliers (km)

    def add(self, rule: str, source: str, severity: str, n_rows: int, action: str) -> None:
        """Record one finding and log it: a warning when rows are concerned and the severity is not "info"."""
        self.entries.append({"rule": rule, "source": source, "severity": severity if n_rows else "none", "rows": int(n_rows), "action": action})
        level = logging.WARNING if n_rows and severity != "info" else logging.INFO
        logger.log(level, "%s [%s] %d row(s) - %s", rule, source, n_rows, action)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.entries, columns=REGISTER_COLUMNS)


def accuracy(schedule: pd.DataFrame) -> dict[str, float]:
    """Share of original (non-imputed) rows, weight and volume in the prepared schedule."""
    modified = schedule["weight_imputed"] | schedule["volume_imputed"] | schedule["distance_imputed"]
    original_weight = schedule["weight"].where(~schedule["weight_imputed"], 0.0)
    original_volume = schedule["volume"].where(~schedule["volume_imputed"], 0.0)
    return {
        "rows": float(len(schedule)),
        "rows_modified": float(modified.sum()),
        "rows_accuracy": 1.0 - float(modified.mean()) if len(schedule) else 1.0,
        "weight_accuracy": float(original_weight.sum() / schedule["weight"].sum()) if len(schedule) else 1.0,
        "volume_accuracy": float(original_volume.sum() / schedule["volume"].sum()) if len(schedule) else 1.0,
    }
