"""Loading of the trucks: whole lines, first fit decreasing (RG04)."""

from __future__ import annotations

import numpy as np

from ..domain.config import PlannerConfig


def pack(weight: np.ndarray, volume: np.ndarray, config: PlannerConfig) -> list[list[int]]:
    """First-fit decreasing on the binding ratio: whole lines go to the first truck where they fit (RG04)."""
    order = np.lexsort((-volume, -weight, -np.maximum(weight / config.truck_kg, volume / config.truck_m3)))
    trucks: list[list[int]] = []
    loads: list[list[float]] = []
    for i in order:
        for t, (kg, m3) in enumerate(loads):
            if kg + weight[i] <= config.truck_kg + 1e-9 and m3 + volume[i] <= config.truck_m3 + 1e-9:
                loads[t][0] += weight[i]
                loads[t][1] += volume[i]
                trucks[t].append(i)
                break
        else:
            loads.append([weight[i], volume[i]])
            trucks.append([i])
    return trucks
