"""Style graphique commun aux notebooks (traits fins, grille discrète, palette fixe)."""
from __future__ import annotations

import matplotlib as mpl

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_MUTED = "#52514e"
GRID = "#e4e3df"
# Palette catégorielle validée, toujours dans cet ordre (une couleur = une usine)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
PRIMARY = SERIES[0]
ACCENT = SERIES[1]


def set_style() -> None:
    mpl.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "figure.dpi": 110, "figure.figsize": (9, 3.6),
        "axes.edgecolor": GRID, "axes.labelcolor": TEXT_MUTED, "axes.titlecolor": TEXT,
        "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.labelsize": 9, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "axes.grid.axis": "y", "axes.axisbelow": True,
        "grid.color": GRID, "grid.linewidth": 0.8,
        "xtick.color": TEXT_MUTED, "ytick.color": TEXT_MUTED,
        "xtick.labelsize": 8, "ytick.labelsize": 8,
        "legend.frameon": False, "legend.fontsize": 8,
        "axes.prop_cycle": mpl.cycler(color=SERIES),
        "patch.linewidth": 0, "lines.linewidth": 2,
    })


def plant_colors(plants) -> dict:
    """Couleur fixe par usine (suit l'entité, pas son rang)."""
    return {p: SERIES[i % len(SERIES)] for i, p in enumerate(sorted(plants))}
