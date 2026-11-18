"""Paramètres du projet : chemins, contraintes métier et règles de préparation.

Toutes les valeurs « métier » sont regroupées ici pour qu'un changement de règle
ne demande jamais de modifier le code des traitements (reproductibilité :
mêmes entrées + mêmes paramètres = mêmes résultats).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

# --------------------------------------------------------------------------- #
# Chemins
# --------------------------------------------------------------------------- #
ROOT_DIR = Path(__file__).resolve().parents[2]
# Sources brutes : `docs/` dans le dépôt GitHub, `documents/` en local
RAW_DIR = next((ROOT_DIR / d for d in ("docs", "documents") if (ROOT_DIR / d / "released_schedule.csv").exists()),
               ROOT_DIR / "docs")
PROCESSED_DIR = ROOT_DIR / "data" / "processed"

SCHEDULE_FILE = RAW_DIR / "released_schedule.csv"
DISTANCES_FILE = RAW_DIR / "distances.csv"
RATE_SHEET_FILE = RAW_DIR / "rate_sheet.pdf"

# --------------------------------------------------------------------------- #
# Contraintes métier (cahier des charges, slide « Contraintes »)
# --------------------------------------------------------------------------- #
TRUCK_MAX_WEIGHT_KG = 24_500.0      # un camion est plein dès qu'une limite est atteinte
TRUCK_MAX_VOLUME_M3 = 110.4
MAX_ANTICIPATION_DAYS = 7           # on peut avancer un besoin d'au plus 7 jours, jamais le retarder
ALLOWED_FREQUENCIES = (1, 2, 3, 5)  # livraisons par semaine
WORKING_WEEKDAYS = (0, 1, 2, 3, 4)  # lundi -> vendredi

# Clés
COUPLE_KEY = ["supplier_id", "plant"]
LINE_KEY = ["date", "supplier_id", "material", "plant", "unloading_point"]

# --------------------------------------------------------------------------- #
# Règles de préparation (décisions documentées dans le notebook M1)
# --------------------------------------------------------------------------- #
# Jours fériés français tombant dans l'horizon du planning. Ils sont seulement
# signalés : des besoins existent ces jours-là, la décision de livrer ou non
# relève du moteur de consolidation.
PUBLIC_HOLIDAYS = frozenset({date(2026, 11, 11), date(2026, 12, 25), date(2027, 1, 1)})

# La dernière date du planning est un « bucket » cumulé de fin d'horizon
# (≈ 7x le volume d'un jour normal, isolée après un trou de dates).
TREAT_LAST_DATE_AS_HORIZON_BUCKET = True

# Imputation de la distance manquante : "median" des fournisseurs du planning.
DISTANCE_IMPUTATION = "median"

# Une ligne dont le poids unitaire s'écarte de plus de ce facteur de la médiane
# du material est signalée (le poids est brut, emballage compris : pas corrigée).
UNIT_WEIGHT_OUTLIER_FACTOR = 10.0

EXPECTED_SCHEDULE_COLUMNS = [
    "date", "supplier_id", "material", "plant", "unloading_point",
    "schedule_quantity", "weight", "volume", "transmission_date", "version_date",
]
EXPECTED_DISTANCE_COLUMNS = ["supplier_id", "distance"]
