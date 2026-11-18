"""Lecture de la grille tarifaire (« up to km », « up to kg »)."""
from __future__ import annotations

import numpy as np

from .loaders import RateSheet


def brackets(bounds, values) -> np.ndarray:
    """Tranche de chaque valeur = plus petite borne >= valeur (bornes = maxima inclusifs)."""
    bounds = np.asarray(sorted(bounds), dtype=float)
    idx = np.searchsorted(bounds, np.asarray(values, dtype=float), side="left")
    if np.any(idx >= len(bounds)):
        raise ValueError("Valeur hors grille")
    return bounds[idx]


def _bracket(bounds, value: float) -> float:
    return float(brackets(bounds, [value])[0])


def base_cost(rates: RateSheet, distance_km: float, weight_kg: float) -> float:
    """Coût de base d'un envoi, lu dans la grille."""
    if distance_km < 0 or weight_kg < 0:
        raise ValueError("Distance et poids doivent être positifs")
    km = _bracket(rates.grid.index, distance_km)
    kg = _bracket(rates.grid.columns, weight_kg)
    return float(rates.grid.loc[km, kg])


def transport_price(rates: RateSheet, distance_km: float, weight_kg: float) -> float:
    """Prix final d'un envoi = coût de base x (1 + marge) x diesel."""
    return base_cost(rates, distance_km, weight_kg) * rates.price_factor
