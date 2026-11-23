"""Add the smoothing model (RG09) to the algorithm presentation, in the style of its existing pages.

Page 15 ("Lisser : comment le plan d'un couple est choisi") is replaced by two pages:
the integer programme of one plant and the cost / smoothing frontier.
The page numbers printed on the following pages are updated.

Usage (from the project root):
    uv run python milestone3/make_smoothing_pages.py <base.pdf> <out.pdf>
`base.pdf` is the presentation without these pages (for example `git show a0e8aba:milestone3/presentation_algorithme.pdf`).
"""

from __future__ import annotations

import io
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
from pypdf import PdfReader, PdfWriter

from delivery_schedule_optimization import planner as pl

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("smoothing_pages")

ROOT = Path(__file__).resolve().parent.parent
REPLACED_PAGE = 15  # page of the base presentation (1-based) replaced by the new pages
SMALL_BUDGET = 0.005  # "petit lissage" scenario of the notebook (0.5 % of the cost)

BLUE, ORANGE, GREEN, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
BLUE_L, ORANGE_L, GREEN_L, GREY_L = "#e8f1fc", "#fdf0ea", "#e6f6ee", "#f3f2ee"
INK, BODY, MUTED, BORDER = "#111111", "#444444", "#9c9a92", "#d9d9d9"
PLANT_COLORS = {"PLT_001": BLUE, "PLT_002": ORANGE, "PLT_003": GREEN, "PLT_004": YELLOW}
plt.rcParams.update({"font.family": "DejaVu Sans", "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": BORDER, "axes.labelcolor": BODY, "xtick.color": BODY, "ytick.color": BODY})


@dataclass(frozen=True)
class PlantFrontier:
    """Cost / smoothing frontier of one plant: (indicator, cost) points, cheapest first."""

    plant: str
    couples: int
    plans: int
    points: list[tuple[int, float]]

    @property
    def free(self) -> tuple[int, float]:
        """Best indicator reached at the lowest cost (free smoothing)."""
        cheapest = self.points[0][1]
        return min((p for p in self.points if p[1] <= cheapest + 1e-6), key=lambda p: p[0])


def eur(v: float) -> str:
    return f"{v:,.0f} €".replace(",", " ")


def frontiers() -> tuple[list[PlantFrontier], float]:
    """Run the planner on the project data and return the frontier of every plant and the cost without smoothing."""
    cfg = pl.PlannerConfig()
    lines = pl.load_lines(ROOT / "data/consolidated_dataset_max.csv", cfg)
    tariff = pl.Tariff(pl.load_rate_grid(ROOT / "docs/rate_sheet.csv"), cfg)
    candidates = pl.candidate_plans(lines, pl.public_holidays(lines, cfg), tariff, cfg)
    chosen = pl.choose_profiles(candidates, cfg)
    options = pl.smoothing_options(candidates, chosen, cfg)
    cost0 = sum(p.cost for p in chosen.values())
    out = []
    for plant in sorted({k[1] for k in options}):
        opts = {k: v for k, v in options.items() if k[1] == plant}
        points = [(int(i), float(c)) for i, c, _ in pl.plant_frontier(opts, SMALL_BUDGET * cost0, cfg)]
        out.append(PlantFrontier(plant, len(opts), sum(len(v) for v in opts.values()), points))
        log.info("%s: %d points, indicator %d -> %d", plant, len(points), points[0][0], points[-1][0])
    return out, cost0


# --------------------------------------------------------------------------- drawing helpers
def page(title: str, subtitle: str, number: int) -> plt.Figure:
    fig = plt.figure(figsize=(13.333, 7.5), facecolor="white")
    fig.patches.append(Rectangle((0, 0.988), 1, 0.012, transform=fig.transFigure, color=BLUE, zorder=5))
    fig.text(0.049, 0.875, title, fontsize=21, fontweight="bold", color=INK)
    fig.text(0.049, 0.808, subtitle, fontsize=12.5, color=BODY)
    fig.text(0.943, 0.045, str(number), fontsize=8.5, color=BODY, ha="center")
    return fig


def card(ax: plt.Axes, x: float, y: float, w: float, h: float, fc: str = "white", ec: str = BORDER) -> None:
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.025", fc=fc, ec=ec, lw=1.2))


def badge(ax: plt.Axes, x: float, y: float, n: int, color: str, title: str) -> None:
    ax.scatter([x], [y], s=420, color=color, zorder=3)
    ax.text(x, y, str(n), zorder=4, ha="center", va="center", color="white", fontsize=11, fontweight="bold")
    ax.text(x + 0.04, y, title, va="center", fontsize=12.5, fontweight="bold", color=INK)


def arrow(ax: plt.Axes, x1: float, y: float, x2: float) -> None:
    ax.add_patch(FancyArrowPatch((x1, y), (x2, y), arrowstyle="-|>", mutation_scale=16, color="#555555", lw=1.4))


def canvas(fig: plt.Figure) -> plt.Axes:
    ax = fig.add_axes([0.04, 0.1, 0.92, 0.68])
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    return ax


# --------------------------------------------------------------------------- pages
def p_model(pdf: PdfPages, number: int, fronts: list[PlantFrontier]) -> None:
    fig = page("Lisser : le modèle mathématique d'une usine",
               "Un programme linéaire en nombres entiers (HiGHS) choisit un plan par couple, pour tous les couples de l'usine à la fois", number)
    ax = canvas(fig)
    cols = [(0.0, BLUE, "Ce qu'on lui donne"), (0.345, ORANGE, "Ce qu'il décide"), (0.69, GREEN, "Ce qu'il calcule")]
    for k, (x, color, title) in enumerate(cols):
        card(ax, x, 0.0, 0.31, 1.0)
        badge(ax, x + 0.04, 0.92, k + 1, color, title)
    arrow(ax, 0.313, 0.5, 0.342); arrow(ax, 0.658, 0.5, 0.687)
    total = sum(f.plans for f in fronts)
    ax.text(0.025, 0.82,
            "Les plans candidats de chaque couple\n(un plan par ensemble de jours, calculé\npar le graphe : environ 12 par couple)\n\n"
            "Pour chaque plan p :\n"
            "  • son coût  $c_p$\n"
            "  • ses camions reçus chaque jour  $n_{p,d}$\n\n"
            f"Sur nos données : {total} plans\npour {sum(f.couples for f in fronts)} couples et 4 usines\n\n"
            "Un niveau de lissage visé  $L$\n(voir page suivante)",
            fontsize=11, color=INK, va="top", linespacing=1.5)
    ax.add_patch(FancyBboxPatch((0.36, 0.555), 0.28, 0.255, boxstyle="round,pad=0,rounding_size=0.02", fc=ORANGE_L, ec="none"))
    ax.text(0.37, 0.795,
            "Variables\n"
            "$x_p$ = 1 si le couple prend le plan p\n"
            "$M_s$ = camions du jour le plus chargé\n           de la semaine s\n"
            "$M$ = camions du jour le plus chargé\n         de toute la période",
            fontsize=11, color=INK, va="top", linespacing=1.5)
    rules = [("$\\Sigma\\, x_p = 1$", "un plan par couple"),
             ("$\\Sigma\\, n_{p,d}\\, x_p \\leq M_s$", "camions du jour d ≤ pic de sa semaine s"),
             ("$M_s \\leq M$", "pic de la semaine ≤ pic de la période"),
             ("$\\Sigma\\, M_s + M \\leq L$", "le niveau de lissage visé")]
    ax.text(0.37, 0.5, "Règles", fontsize=11, color=INK, va="top")
    for k, (formula, text) in enumerate(rules):
        y = 0.43 - k * 0.075
        ax.text(0.37, y, formula, fontsize=11, color=INK, va="top")
        ax.text(0.37, y - 0.035, text, fontsize=9.5, color=BODY, va="top")
    ax.text(0.37, 0.09, "Objectif :  minimiser  $\\Sigma\\, c_p\\, x_p$  (le coût)", fontsize=12, fontweight="bold", color=ORANGE, va="top")
    ax.add_patch(FancyBboxPatch((0.705, 0.48), 0.28, 0.33, boxstyle="round,pad=0,rounding_size=0.02", fc=GREEN_L, ec="none"))
    ax.text(0.715, 0.795,
            "L'indicateur de lissage\n\n"
            "$\\Sigma\\, M_s + M$\n"
            "= les pics des semaines, additionnés,\n   + le pic de toute la période\n\n"
            "Plus il est bas, mieux les camions\nsont répartis sur la semaine.",
            fontsize=11, color=INK, va="top", linespacing=1.5)
    ax.text(0.715, 0.4,
            "Pourquoi chaque semaine ?\n"
            "Avec le seul pic global, une semaine\n"
            "très chargée (le démarrage) cache\n"
            "toutes les autres : le solveur n'aurait\n"
            "plus aucune raison de les lisser.\n\n"
            "Résultat : le plan le moins cher\n"
            "dont l'indicateur est au plus L.",
            fontsize=11, color=INK, va="top", linespacing=1.5)
    fig.text(0.5, 0.06, "Les règles des couples (fenêtre de 7 jours, mêmes jours, profil) sont déjà respectées par chaque plan candidat : "
             "le lissage ne fait que choisir.", ha="center", fontsize=9.5, color=BODY)
    pdf.savefig(fig); plt.close(fig)


def p_frontier(pdf: PdfPages, number: int, fronts: list[PlantFrontier]) -> None:
    f = next(x for x in fronts if x.plant == "PLT_002")
    fig = page("Lisser : resserrer pas à pas (la frontière)",
               f"On relance le modèle en baissant L d'un cran à chaque fois · exemple réel {f.plant} ({f.couples} couples, {f.plans} plans)", number)
    ax = canvas(fig)
    card(ax, 0.0, 0.0, 0.36, 1.0)
    badge(ax, 0.04, 0.92, 1, BLUE, "La boucle")
    steps = [("1", "Sans niveau visé : le plan le\nmoins cher de l'usine.", BLUE_L),
             ("2", "On note son indicateur I, puis\non impose  L = I − 1  et on relance.", GREY_L),
             ("3", "On recommence tant que le coût\nreste dans le budget.", GREY_L),
             ("4", "Stop dès que le budget est dépassé\nou que L est impossible.", GREEN_L)]
    for k, (n, text, fc) in enumerate(steps):
        y = 0.73 - k * 0.19
        ax.add_patch(FancyBboxPatch((0.025, y), 0.31, 0.15, boxstyle="round,pad=0,rounding_size=0.015", fc=fc, ec="none"))
        ax.text(0.04, y + 0.075, n, fontsize=14, fontweight="bold", color=BLUE, va="center")
        ax.text(0.07, y + 0.075, text, fontsize=10.5, color=INK, va="center", linespacing=1.4)
    ax.text(0.025, 0.04, "Chaque niveau est résolu à l'optimum (gap 0).", fontsize=9.5, color=BODY)
    bx = fig.add_axes([0.46, 0.2, 0.5, 0.52])
    base_i, base_c = f.points[0]
    extra = [c - base_c for _, c in f.points]
    ind = [i for i, _ in f.points]
    bx.step(ind, extra, where="post", color=ORANGE, lw=2)
    bx.scatter(ind, extra, color=ORANGE, s=14, zorder=3)
    fi, fc = f.free
    bx.scatter([base_i], [0], s=90, color=MUTED, zorder=4)
    bx.annotate(f"plan le moins cher\nindicateur {base_i}", (base_i, 0), (base_i - 3, max(extra) * 0.2), fontsize=10, ha="left", color=BODY,
                arrowprops={"arrowstyle": "-", "color": MUTED})
    bx.scatter([fi], [fc - base_c], s=90, color=BLUE, zorder=4)
    bx.annotate(f"lissage gratuit (budget 0)\nindicateur {fi}, même coût", (fi, 0), (fi + 4, max(extra) * 0.55), fontsize=10, color=BLUE,
                arrowprops={"arrowstyle": "-", "color": BLUE})
    li, lc = f.points[-1]
    bx.scatter([li], [lc - base_c], s=90, color=GREEN, zorder=4)
    bx.annotate(f"budget 0,5 % atteint\nindicateur {li}, +{eur(lc - base_c)}", (li, lc - base_c), (li + 10, (lc - base_c) * 0.9), fontsize=10, color=GREEN, ha="right",
                arrowprops={"arrowstyle": "-", "color": GREEN})
    bx.invert_xaxis()
    bx.set_xlabel("indicateur de lissage  (← plus serré : mieux réparti)", fontsize=10)
    bx.set_ylabel("surcoût par rapport au moins cher (€)", fontsize=10)
    bx.set_title("chaque point = le plan le moins cher pour un niveau L", fontsize=11, color=INK, loc="left")
    bx.grid(axis="y", color="#eeeeee")
    fig.text(0.5, 0.06, f"Les premiers crans sont gratuits : de {base_i} à {fi}, on ne fait que choisir entre des plans de même coût. "
             "Ensuite, chaque cran coûte de plus en plus cher.", ha="center", fontsize=9.5, color=BODY)
    pdf.savefig(fig); plt.close(fig)


def number_overlay(number: int, width: float, height: float) -> PdfReader:
    """A transparent page that hides the old page number and prints the new one."""
    fig = plt.figure(figsize=(width / 72, height / 72))
    fig.patch.set_alpha(0)
    fig.patches.append(Rectangle((0.925, 0.03), 0.04, 0.04, transform=fig.transFigure, color="white", zorder=5))
    fig.text(0.943, 0.045, str(number), fontsize=8.5, color=BODY, ha="center", zorder=6)
    buf = io.BytesIO()
    fig.savefig(buf, format="pdf", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return PdfReader(buf)


if __name__ == "__main__":
    base, out = Path(sys.argv[1]), Path(sys.argv[2])
    fronts, _ = frontiers()
    new = io.BytesIO()
    with PdfPages(new) as pdf:
        p_model(pdf, REPLACED_PAGE, fronts)
        p_frontier(pdf, REPLACED_PAGE + 1, fronts)
    new.seek(0)
    reader, added = PdfReader(base), PdfReader(new)
    writer = PdfWriter()
    for k, pg in enumerate(reader.pages, start=1):
        if k == REPLACED_PAGE:
            for extra in added.pages:
                writer.add_page(extra)
            continue
        kept = writer.add_page(pg)
        if k > REPLACED_PAGE:
            box = kept.mediabox
            kept.merge_page(number_overlay(len(writer.pages), float(box.width), float(box.height)).pages[0])
    writer.add_metadata(reader.metadata or {})
    with out.open("wb") as fh:
        writer.write(fh)
    log.info("presentation written: %s (%d pages)", out, len(writer.pages))
