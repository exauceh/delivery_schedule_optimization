"""Chargement des trois sources brutes, sans aucune transformation métier."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import config


def _check_columns(df: pd.DataFrame, expected: list[str], name: str) -> None:
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise ValueError(f"{name} : colonnes manquantes {missing}")


def load_schedule(path: Path | str = config.SCHEDULE_FILE) -> pd.DataFrame:
    """Charge le release schedule et ajoute `line_id` (n° de ligne source) pour la traçabilité."""
    df = pd.read_csv(
        path,
        encoding="utf-8-sig",  # le fichier commence par un BOM
        parse_dates=["date", "transmission_date", "version_date"],
        dtype={"supplier_id": "string", "material": "string",
               "plant": "string", "unloading_point": "string"},
    )
    _check_columns(df, config.EXPECTED_SCHEDULE_COLUMNS, "released_schedule")
    df.insert(0, "line_id", np.arange(len(df)))
    return df


def load_distances(path: Path | str = config.DISTANCES_FILE) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={"supplier_id": "string"})
    _check_columns(df, config.EXPECTED_DISTANCE_COLUMNS, "distances")
    df["distance"] = pd.to_numeric(df["distance"], errors="coerce")
    return df


# --------------------------------------------------------------------------- #
# Rate sheet (PDF)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RateSheet:
    """Grille tarifaire : coût de base (EUR / envoi) par tranche de distance et de poids.

    `grid` : index = borne haute km, colonnes = borne haute kg ; la dernière
    tranche ouverte (« >650 », « >4000 ») est codée `inf`.
    """
    grid: pd.DataFrame
    margin: float          # 2.3923 pour 239.23 %
    diesel_surcharge: float
    valid_from: date | None

    @property
    def price_factor(self) -> float:
        """Facteur appliqué au coût de base : (1 + marge) x diesel."""
        return (1.0 + self.margin) * self.diesel_surcharge

    def to_long(self) -> pd.DataFrame:
        """Grille au format long (km_max, kg_max, base_cost) pour l'export."""
        out = self.grid.stack().rename("base_cost").reset_index()
        out.columns = ["km_max", "kg_max", "base_cost"]
        return out


def _to_bound(token: str) -> float:
    token = token.strip()
    return math.inf if token.startswith(">") else float(token.replace(",", ""))


def parse_rate_sheet_text(text: str) -> RateSheet:
    """Analyse le texte extrait du PDF. Séparé du PDF pour pouvoir être testé."""
    start = text.index("km \\ kg") + len("km \\ kg")
    end = text.index("Freight value margin")
    tokens = text[start:end].split()

    # L'en-tête kg se termine par la tranche ouverte (« >4000 »)
    n_cols = next(i for i, t in enumerate(tokens) if t.startswith(">")) + 1
    kg_bounds = [_to_bound(t) for t in tokens[:n_cols]]
    body = tokens[n_cols:]
    if len(body) % (n_cols + 1):
        raise ValueError("Rate sheet : nombre de cellules incohérent avec l'en-tête")

    rows = {}
    for i in range(0, len(body), n_cols + 1):
        km = _to_bound(body[i])
        rows[km] = [float(v.replace(",", "")) for v in body[i + 1:i + 1 + n_cols]]
    grid = pd.DataFrame.from_dict(rows, orient="index", columns=kg_bounds)
    grid.index.name, grid.columns.name = "km_max", "kg_max"

    margin = float(re.search(r"margin:\s*([\d.,]+)\s*%", text).group(1).replace(",", ".")) / 100
    diesel = float(re.search(r"Diesel surcharge:\s*([\d.,]+)", text).group(1).replace(",", "."))
    m = re.search(r"Valid from:\s*(\d{2}\.\d{2}\.\d{4})", text)
    valid_from = datetime.strptime(m.group(1), "%d.%m.%Y").date() if m else None
    return RateSheet(grid=grid, margin=margin, diesel_surcharge=diesel, valid_from=valid_from)


def extract_pdf_text(path: Path | str) -> str:
    import logging
    from pypdf import PdfReader
    # La copie du PDF dans le dépôt a un index (xref) légèrement abîmé : pypdf le
    # reconstruit seul et le contenu est identique, on masque donc ses avertissements.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def load_rate_sheet(path: Path | str = config.RATE_SHEET_FILE) -> RateSheet:
    return parse_rate_sheet_text(extract_pdf_text(path))
