"""Visual PDF of the optimisation engine (milestone 3), built from the project's real data.

Run from the project root after the notebook has exported data/optimized_*.csv:
    uv run python milestone3/make_optim_pdf.py [png_dir]
The optional png_dir also saves one PNG per page for a quick visual check.
"""

from __future__ import annotations

import logging
import re
import sys
import tempfile
from pathlib import Path

import holidays
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pulp
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

from delivery_schedule_optimization import engine
from delivery_schedule_optimization.engine import CoupleInstance, SolverSettings, solve_couple

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("optim_pdf")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "milestone3" / "optimiseur_visuel.pdf"
PNG_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else None

BG, INK, BODY, MUTED = "#fffbed", "#1a1a1a", "#595959", "#b0b0b0"
YELLOW, YELLOW_L, YELLOW_D, GREY_L = "#ffdc4a", "#fff6b6", "#af7e04", "#e7e7e7"
GREEN, GREEN_L, GREEN_D = "#2dc75c", "#adf0c7", "#067429"
SEQ = LinearSegmentedColormap.from_list("seq", ["#fffbed", YELLOW, "#9b4a08"])
plt.rcParams.update({"font.family": "DejaVu Sans", "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": MUTED, "axes.labelcolor": BODY, "xtick.color": BODY, "ytick.color": BODY,
                     "axes.facecolor": BG, "figure.facecolor": BG, "savefig.facecolor": BG})
FACTOR = (1 + 2.3923) * 1.1943
DAYS_FR = ["lun", "mar", "mer", "jeu", "ven"]


def eur(v: float) -> str:
    return f"{v:,.0f} €".replace(",", " ")


def page(title: str, kicker: str, subtitle: str = "") -> plt.Figure:
    fig = plt.figure(figsize=(13.33, 7.5))
    fig.text(0.05, 0.93, kicker.upper(), fontsize=11, fontweight="bold", color=YELLOW_D)
    fig.text(0.05, 0.875, title, fontsize=24, fontweight="bold", color=INK)
    if subtitle:
        fig.text(0.05, 0.83, subtitle, fontsize=12.5, color=BODY)
    return fig


def note(fig: plt.Figure, text: str, y: float = 0.03) -> None:
    import textwrap
    fig.text(0.05, y, chr(10).join(textwrap.wrap(text, 148)), fontsize=11, color=BODY, va="bottom", linespacing=1.4)


def box(ax: plt.Axes, x: float, y: float, w: float, h: float, text: str, fc: str = YELLOW_L, size: float = 12,
        bold: bool = False, color: str = INK) -> None:
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.01,rounding_size=0.02", fc=fc, ec="none"))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size, color=color,
            fontweight="bold" if bold else "normal", wrap=True)


def arrow(ax: plt.Axes, x1: float, y1: float, x2: float, y2: float) -> None:
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=18, color="#313131", lw=1.6))


# --------------------------------------------------------------------------- data
needs = pd.read_csv(ROOT / "data/consolidated_dataset.csv", parse_dates=["date"]).reset_index(names="line_id")
lines = pd.read_csv(ROOT / "data/optimized_lines.csv", parse_dates=["date", "delivery_date"])
trucks = pd.read_csv(ROOT / "data/optimized_trucks.csv", parse_dates=["delivery_date"])
couples = pd.read_csv(ROOT / "data/optimized_couples.csv")
plant_load = pd.read_csv(ROOT / "data/optimized_plant_load.csv", parse_dates=["day"])
baseline = pd.read_csv(ROOT / "data/baseline_deliveries.csv", parse_dates=["date"])
deliveries = pd.read_csv(ROOT / "data/optimized_deliveries.csv", parse_dates=["delivery_date"])
kpis = pd.read_csv(ROOT / "data/optimized_kpis.csv", index_col=0)
grid = pd.read_csv(ROOT / "docs/rate_sheet.csv", dtype={"km_up_to": str}).set_index("km_up_to").astype(float)
KM = np.array([np.inf if k.startswith(">") else float(k) for k in grid.index])
KG = np.array([np.inf if k.startswith(">") else float(k) for k in grid.columns])
UPPER = np.where(np.isinf(KG), 24_500.0, KG)
BASE_COST = float(kpis.loc[kpis.index[-1], kpis.columns[0]])
FINAL_COST = float(kpis.loc[kpis.index[-1], kpis.columns[1]])


def solver_log(supplier: str, plant: str, n_max: int) -> pd.DataFrame:
    """Solve one couple with the engine model and read the HiGHS log: best bound and best solution over time."""
    group = needs[(needs.supplier_id == supplier) & (needs.plant == plant)]
    d = float(group["distance"].iloc[0])
    inst = CoupleInstance(supplier, plant, d, group["line_id"].to_numpy(np.int64),
                          group["date"].to_numpy().astype("datetime64[D]"), group["weight"].to_numpy(float),
                          group["volume"].to_numpy(float), grid.to_numpy()[np.searchsorted(KM, d)] * FACTOR, UPPER)
    de = holidays.country_holidays("DE", years=[2026, 2027])
    cal = np.array([x for x in pd.bdate_range(needs["date"].min(), needs["date"].max()) if x not in de],
                   dtype="datetime64[D]")
    log_path = Path(tempfile.gettempdir()) / "optim_pdf_highs.log"
    log_path.unlink(missing_ok=True)
    original = pulp.HiGHS

    def logging_highs(*args: object, **kwargs: object) -> pulp.HiGHS:
        kwargs.update(msg=True, log_file=str(log_path), log_to_console=False, time_limit=30.0)
        kwargs.pop("timeLimit", None)
        return original(*args, **kwargs)

    engine.pulp.HiGHS = logging_highs  # type: ignore[assignment]
    try:
        sol = solve_couple(inst, n_max, cal, SolverSettings(24_500.0, 110.4, 7, 0.01, 30.0, 0.0001))
    finally:
        engine.pulp.HiGHS = original  # type: ignore[assignment]
    rows = []
    for line in log_path.read_text(errors="ignore").splitlines():
        parts = line.split()
        if len(parts) >= 12 and parts[-1].endswith("s") and "%" in line and parts[-1][:-1].replace(".", "").isdigit():
            nums = [x for x in parts if x not in "JRCLTBUHZSPFI"]
            try:
                bound = float(nums[4]) if nums[4] not in ("-inf", "inf") else np.nan
                best = float(nums[5]) if nums[5] not in ("-inf", "inf") else np.nan
            except ValueError:
                continue
            rows.append({"bound": bound, "best": best, "gap": nums[6], "time": float(parts[-1][:-1])})
    log.info("solver log: %d progress lines, final cost %.0f", len(rows), sol.model_cost)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- pages
def p_overview(pdf: PdfPages) -> None:
    fig = page("Le moteur d'optimisation en un coup d'œil", "L'optimiseur",
               "Trois étapes enchaînées. Chaque étape prend le résultat de la précédente.")
    ax = fig.add_axes([0.04, 0.12, 0.92, 0.66]); ax.set_xlim(-0.01, 1.01); ax.set_ylim(0, 1); ax.axis("off")
    steps = [
        ("Besoins", "4 389 commandes\n180 fournisseurs-usines\n6 semaines", GREY_L),
        ("1 · Choisir les jours", "900 MILP\n(180 × 5 profils)\njours fixes + jour de\nchaque commande", YELLOW),
        ("2 · Répartir", "un MILP par usine\nmoins de camions le\njour le plus chargé", YELLOW),
        ("3 · Remplir", "un petit MILP par\nlivraison : commandes\nentières, prix minimal", YELLOW),
        ("Plan final", f"{eur(FINAL_COST)}\n658 livraisons\n765 camions", GREEN_L),
    ]
    w, gap = 0.155, 0.045
    for i, (t, body, fc) in enumerate(steps):
        x = 0.01 + i * (w + gap)
        ax.add_patch(FancyBboxPatch((x, 0.25), w, 0.55, boxstyle="round,pad=0.01,rounding_size=0.03", fc=fc, ec="none"))
        ax.text(x + w / 2, 0.72, t, ha="center", va="center", fontsize=12.5, fontweight="bold", color=INK)
        ax.text(x + w / 2, 0.47, body, ha="center", va="center", fontsize=11.5, color=INK, linespacing=1.5)
        if i < len(steps) - 1:
            arrow(ax, x + w + 0.005, 0.525, x + w + gap - 0.005, 0.525)
    ax.text(0.5, 0.1, "environ 2 min 45 au total, sur les 12 cœurs de l'ordinateur", ha="center", fontsize=12, color=BODY)
    note(fig, "MILP = programme linéaire en nombres entiers : on décrit les décisions (oui/non), les règles et le prix, "
              "et le solveur HiGHS trouve la combinaison la moins chère.")
    pdf.savefig(fig); save_png(fig, 1); plt.close(fig)


def p_decisions(pdf: PdfPages) -> None:
    sup, plant = "SUP_00085", "PLT_004"
    g = lines[(lines.supplier_id == sup) & (lines.plant == plant) & (lines.date <= "2026-11-27")].sort_values(["date", "material"])
    g = g.iloc[np.linspace(0, len(g) - 1, 40).round().astype(int)]
    days = pd.bdate_range("2026-11-09", "2026-11-27")
    fig = page("Ce que le MILP décide : le jour de chaque commande", "Étape 1 · les décisions",
               f"{sup} vers {plant}, 40 commandes des 3 premières semaines. Une ligne = une commande, une colonne = un jour de livraison.")
    ax = fig.add_axes([0.10, 0.13, 0.62, 0.66])
    mat = np.zeros((len(g), len(days)))
    for i, r in enumerate(g.itertuples()):
        for j, d in enumerate(days):
            if 0 <= (r.date - d).days <= 7:
                mat[i, j] = 1  # possible day
            if d == r.date:
                mat[i, j] = 2  # need day
            if d == r.delivery_date:
                mat[i, j] = 3  # chosen day
    from matplotlib.colors import BoundaryNorm, ListedColormap
    cmap = ListedColormap([BG, YELLOW_L, "#f8d3af", YELLOW_D])
    ax.imshow(mat, aspect="auto", cmap=cmap, norm=BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5], 4), interpolation="nearest")
    ax.set_xticks(range(len(days)), [f"{DAYS_FR[d.dayofweek]}\n{d:%d/%m}" for d in days], fontsize=8)
    ax.set_yticks([]); ax.set_ylabel("commandes (triées par date de besoin)", fontsize=11)
    for j, d in enumerate(days):
        if d.dayofweek == 0:
            ax.axvline(j - 0.5, color=MUTED, lw=0.8)
    lx = fig.add_axes([0.75, 0.13, 0.22, 0.66]); lx.axis("off"); lx.set_xlim(0, 1); lx.set_ylim(0, 1)
    for k, (fc, t) in enumerate([(YELLOW_D, "jour choisi par le MILP"), ("#f8d3af", "jour du besoin"),
                                 (YELLOW_L, "jour possible\n(7 jours d'avance au plus)")]):
        lx.add_patch(Rectangle((0.02, 0.9 - k * 0.12), 0.1, 0.07, fc=fc, ec=MUTED, lw=0.5))
        lx.text(0.16, 0.935 - k * 0.12, t, va="center", fontsize=11, color=INK)
    lx.text(0.02, 0.45, "Chaque case jaune est une\nquestion oui/non :\n« cette commande part-elle\nce jour-là ? »\n\n"
            "Le MILP choisit une seule\ncase par ligne, et les mêmes\njours chaque semaine :\nici, le lundi.",
            fontsize=11.5, color=INK, va="top", linespacing=1.4)
    note(fig, "Pour ce fournisseur : environ 200 commandes et 5 à 6 jours possibles chacune, soit plus de 1 000 questions oui/non.")
    pdf.savefig(fig); save_png(fig, 2); plt.close(fig)


def p_cost(pdf: PdfPages) -> None:
    fig = page("Le prix d'un camion : ce que le MILP minimise", "Étape 1 · l'objectif",
               "Prix d'un camion selon son poids, pour un fournisseur à 121 km (grille × marge × diesel).")
    ax = fig.add_axes([0.08, 0.14, 0.52, 0.64])
    prices = grid.loc["125"].to_numpy() * FACTOR
    edges = np.r_[0, UPPER]
    for k in range(len(prices)):
        ax.fill_between([edges[k] / 1000, edges[k + 1] / 1000], 0, prices[k], step="pre",
                        color=YELLOW if k == len(prices) - 1 else YELLOW_L, ec=YELLOW_D, lw=0.6)
    ax.set_xlabel("poids du camion (tonnes)"); ax.set_ylabel("prix du camion (€)")
    ax.set_xlim(0, 25); ax.set_ylim(0, 380)
    ax.annotate("forfait : 331 € de 4 t à 24,5 t", (14, 331), (8, 360), fontsize=11, color=INK,
                arrowprops=dict(arrowstyle="-", color=MUTED))
    ax.annotate("1 t : 147 €", (1, 147), (3, 200), fontsize=11, color=INK, arrowprops=dict(arrowstyle="-", color=MUTED))
    rx = fig.add_axes([0.64, 0.14, 0.33, 0.64]); rx.axis("off"); rx.set_xlim(0, 1); rx.set_ylim(0, 1)
    rx.text(0, 0.97, "Exemple : 28 t le même jour", fontsize=14, fontweight="bold", color=INK, va="top")
    rx.add_patch(Rectangle((0, 0.62), 0.9, 0.2, fc="white", ec=MUTED)); rx.add_patch(Rectangle((0.02, 0.64), 0.86, 0.16, fc=YELLOW, ec="none"))
    rx.text(0.45, 0.72, "camion plein · 24,5 t · 331 €", ha="center", va="center", fontsize=11, color=INK)
    rx.add_patch(Rectangle((0, 0.36), 0.9, 0.2, fc="white", ec=MUTED)); rx.add_patch(Rectangle((0.02, 0.38), 0.12, 0.16, fc=YELLOW, ec="none"))
    rx.text(0.52, 0.46, "dernier camion · 3,5 t · 297 €", ha="center", va="center", fontsize=11, color=INK)
    rx.text(0, 0.22, "= 628 € pour la journée", fontsize=16, fontweight="bold", color=INK)
    rx.text(0, 0.02, "Le MILP compte, chaque jour, des camions\npleins au forfait, puis un dernier camion\nau prix de sa tranche, et additionne.",
            fontsize=11.5, color=BODY, linespacing=1.4)
    note(fig, "Leçon : un camion vide coûte presque autant qu'un camion plein. Regrouper fait baisser le prix au kilo.")
    pdf.savefig(fig); save_png(fig, 3); plt.close(fig)


def p_convergence(pdf: PdfPages) -> None:
    sup, plant = "SUP_00129", "PLT_003"
    df = solver_log(sup, plant, 3)
    fig = page("Comment le solveur trouve la solution", "Étape 1 · la résolution",
               f"Journal réel du solveur HiGHS pour {sup} vers {plant} (profil 3 jours), le plus gros fournisseur.")
    ax = fig.add_axes([0.08, 0.14, 0.55, 0.64])
    if len(df):
        df = df.dropna(subset=["best"]).drop_duplicates(["bound", "best"])
        end = df["time"].iloc[-1] + 0.15
        t = list(df["time"]) + [end]
        ax.step(t, list(df["best"]) + [df["best"].iloc[-1]], where="post", color=INK, lw=2.4, label="plafond : meilleur vrai planning trouvé")
        b = df["bound"].ffill()
        ax.step(t, list(b) + [b.iloc[-1]], where="post", color=YELLOW_D, lw=2.4, label="plancher : prix impossible à battre")
        ax.fill_between(t, list(b.fillna(df["best"].min() * 0.85)) + [b.iloc[-1]], list(df["best"]) + [df["best"].iloc[-1]],
                        step="post", color=YELLOW_L, alpha=0.9)
        for r in df.itertuples():
            if pd.notna(r.bound) and r.gap != "0.00%":
                ax.annotate(f"écart {r.gap}", (r.time, (r.bound + r.best) / 2), xytext=(6, 0), textcoords="offset points",
                            fontsize=10, color=BODY, va="center")
        ax.annotate(f"premier vrai planning : {eur(df['best'].iloc[0])}", (df["time"].iloc[0], df["best"].iloc[0]),
                    xytext=(10, -14), textcoords="offset points", fontsize=10, color=INK)
        ax.annotate(f"optimum prouvé : {eur(df['best'].iloc[-1])}", (end, df["best"].iloc[-1]), xytext=(-150, 24),
                    textcoords="offset points", fontsize=11, fontweight="bold", color=INK,
                    arrowprops=dict(arrowstyle="-", color=MUTED))
        ax.set_xlabel("temps de calcul (secondes)"); ax.set_ylabel("coût du planning (€)")
        ax.set_ylim(df["bound"].min() * 0.95, df["best"].max() * 1.04)
        ax.legend(frameon=False, fontsize=11, loc="lower right")
    rx = fig.add_axes([0.66, 0.14, 0.31, 0.64]); rx.axis("off"); rx.set_xlim(0, 1); rx.set_ylim(0, 1)
    txt = ("Plancher : on fait comme si on\npouvait couper les commandes.\nImpossible en vrai, mais rapide :\naucun planning ne peut faire mieux.\n\n"
           "Plafond : un vrai planning qui\nrespecte toutes les règles.\n\n"
           "Séparer et éliminer : le solveur\nteste « la commande 12 part lundi,\noui ou non ? », et abandonne toute\nfamille dont le plancher dépasse\nle meilleur planning connu.\n\n"
           "Il s'arrête quand l'écart (zone\njaune) passe sous 1 %.")
    rx.text(0, 1, txt, fontsize=11.5, color=INK, va="top", linespacing=1.4)
    note(fig, "Dans le moteur, chaque problème a au plus 5 s et s'arrête à 1 % d'écart. Ici on laisse tourner plus longtemps pour voir la courbe entière.")
    pdf.savefig(fig); save_png(fig, 4); plt.close(fig)


def p_profiles(pdf: PdfPages) -> None:
    fig = page("Combien de jours par semaine ?", "Étape 1 · le choix du profil",
               "Le MILP est résolu 5 fois par fournisseur (1 à 5 jours fixes). On garde le moins de jours au meilleur prix.")
    examples = [("SUP_00085", "PLT_004"), ("SUP_00146", "PLT_002"), ("SUP_00129", "PLT_003")]
    for k, (s, p) in enumerate(examples):
        r = couples[(couples.supplier_id == s) & (couples.plant == p)].iloc[0]
        costs = [r[f"cost_N{n}"] for n in range(1, 6)]
        ax = fig.add_axes([0.06 + k * 0.31, 0.2, 0.26, 0.52])
        best = min(costs)
        chosen = next(n for n in range(5) if costs[n] <= best + 1e-6)
        ax.bar(range(1, 6), costs, color=[YELLOW if n == chosen else (MUTED if costs[n] > best + 1e-6 else GREY_L) for n in range(5)])
        for n, c in enumerate(costs):
            ax.text(n + 1, c, eur(c), ha="center", va="bottom", fontsize=9, color=INK)
        ax.set_ylim(0, max(costs) * 1.18); ax.set_xticks(range(1, 6), [f"{n} j" for n in range(1, 6)])
        ax.set_title(f"{s} → {p}\nchoisi : {chosen + 1} jour{'s' if chosen else ''}", fontsize=12, color=INK, loc="left")
        ax.set_yticks([])
    counts = couples["profile"].value_counts().sort_index()
    note(fig, "Jaune : profil choisi. Gris foncé : plus cher. Gris clair : même prix mais plus de jours, donc inutile. "
              f"Résultat sur 180 fournisseurs-usines : {', '.join(f'{v} à {k} jour{"s" if k > 1 else ""}' for k, v in counts.items())}.")
    pdf.savefig(fig); save_png(fig, 5); plt.close(fig)


def p_smoothing(pdf: PdfPages) -> None:
    fig = page("Étape 2 : répartir les camions sur la semaine", "Étape 2 · le lissage",
               "Camions reçus par usine et par jour. Plus la case est foncée, plus le quai est chargé.")
    days = sorted(plant_load["day"].unique())
    days = [d for d in days if pd.Timestamp(d) <= pd.Timestamp("2026-12-08")]
    vmax = plant_load[["avant lissage", "après lissage"]].max().max()
    for k, (col, title) in enumerate([("avant lissage", "après l'étape 1 (chacun choisit seul)"), ("après lissage", "après l'étape 2 (choix commun par usine)")]):
        ax = fig.add_axes([0.08, 0.5 - k * 0.34, 0.86, 0.22])
        m = plant_load[plant_load["day"].isin(days)].pivot(index="plant", columns="day", values=col)
        ax.imshow(m.to_numpy(), aspect="auto", cmap=SEQ, vmin=0, vmax=vmax)
        for i in range(m.shape[0]):
            for j in range(m.shape[1]):
                v = int(m.iat[i, j])
                if v:
                    ax.text(j, i, v, ha="center", va="center", fontsize=8, color="white" if v > vmax * 0.55 else INK)
        ax.set_yticks(range(m.shape[0]), m.index, fontsize=10)
        ax.set_xticks(range(len(days)), [f"{DAYS_FR[pd.Timestamp(d).dayofweek]}\n{pd.Timestamp(d):%d/%m}" for d in days], fontsize=7)
        ax.set_title(title, fontsize=12, color=INK, loc="left")
        for spine in ax.spines.values():
            spine.set_visible(False)
    peaks = plant_load.groupby("plant")[["avant lissage", "après lissage"]].max()
    note(fig, "Pour chaque fournisseur, on calcule le prix de chaque choix de jours ; un MILP par usine choisit pour que le jour le plus chargé "
              f"reçoive le moins de camions, avec au plus 1 % de surcoût. Pic : PLT_002 {peaks.loc['PLT_002', 'avant lissage']} → {peaks.loc['PLT_002', 'après lissage']}, "
              f"PLT_004 {peaks.loc['PLT_004', 'avant lissage']} → {peaks.loc['PLT_004', 'après lissage']}.")
    pdf.savefig(fig); save_png(fig, 6); plt.close(fig)


def p_loading(pdf: PdfPages) -> None:
    per = trucks.groupby(["supplier_id", "plant", "delivery_date"]).agg(n=("truck_no", "size"), lines=("lines", "sum"), w=("weight", "sum")).reset_index()
    cost_spread = trucks.groupby(["supplier_id", "plant", "delivery_date"])["cost"].agg(lambda c: c.max() - c.min()).rename("spread").reset_index()
    per = per.merge(cost_spread, on=["supplier_id", "plant", "delivery_date"])
    pick = per[(per.supplier_id == "SUP_00046") & (per.plant == "PLT_004") & (per.delivery_date == "2026-11-25")].iloc[0]
    s, p, d = pick.supplier_id, pick.plant, pick.delivery_date
    g = lines[(lines.supplier_id == s) & (lines.plant == p) & (lines.delivery_date == d)]
    t = trucks[(trucks.supplier_id == s) & (trucks.plant == p) & (trucks.delivery_date == d)].set_index("truck_no")
    fig = page("Étape 3 : ranger les commandes dans les camions", "Étape 3 · le chargement",
               f"Livraison réelle : {s} vers {p}, le {d:%d/%m}. {len(g)} commandes, {g.weight.sum() / 1000:.1f} t, {len(t)} camions.")
    ax = fig.add_axes([0.08, 0.2, 0.6, 0.56])
    palette = [YELLOW, "#f8d3af", YELLOW_L, "#fe9f4d"]
    for row, (no, sub) in enumerate(g.sort_values("weight", ascending=False).groupby("truck_no")):
        left = 0.0
        for k, w in enumerate(sub["weight"].sort_values(ascending=False) / 1000):
            ax.barh(row, w, left=left, color=palette[k % len(palette)], ec="white", lw=1)
            left += w
        ax.text(24.8, row, f"{eur(t.loc[no, 'cost'])}", va="center", fontsize=12, fontweight="bold", color=INK)
    ax.axvline(24.5, color=INK, lw=1.4, ls="--"); ax.text(24.5, -0.65, "24,5 t max", ha="right", fontsize=10, color=INK)
    ax.set_yticks(range(len(t)), [f"camion {i}" for i in t.index]); ax.invert_yaxis()
    ax.set_xlim(0, 28); ax.set_xlabel("poids chargé (tonnes) · chaque bloc = une commande entière")
    rx = fig.add_axes([0.72, 0.2, 0.25, 0.56]); rx.axis("off")
    rx.text(0, 1, "Les règles du rangement", fontsize=14, fontweight="bold", color=INK, va="top")
    rx.text(0, 0.88, "• une commande ne se coupe\n  jamais\n• 24,5 t et 110,4 m³ au plus\n  par camion\n• chaque camion est payé\n  selon son propre poids\n"
            "• on garde le rangement le\n  moins cher", fontsize=12, color=INK, va="top", linespacing=1.5)
    note(fig, "Ici, la plus grosse commande ne rentre avec aucune autre : 3 camions sont nécessaires, dont 2 incomplets. Tous dépassent 4 t, donc paient le forfait. Plus de 90 % des livraisons tiennent dans un seul camion.")
    pdf.savefig(fig); save_png(fig, 7); plt.close(fig)


def p_waterfall(pdf: PdfPages) -> None:
    first_pass = 110_123.0  # loaded cost after step 1 (notebook, section 10.1)
    fig = page("Ce que chaque étape apporte", "Résultat de l'optimisation", "Coût du transport sur 6 semaines, toutes usines.")
    ax = fig.add_axes([0.08, 0.15, 0.55, 0.62])
    steps = [("aujourd'hui", BASE_COST, MUTED), ("étape 1 :\njours regroupés", first_pass - BASE_COST, GREEN),
             ("étape 2 :\nrépartition", FINAL_COST - first_pass, "#fe9f4d"), ("plan final", FINAL_COST, YELLOW)]
    level = 0.0
    for i, (lab, v, c) in enumerate(steps):
        if i in (0, 3):
            ax.bar(i, v, color=c, width=0.6); ax.text(i, v + 2000, eur(v), ha="center", fontsize=12, fontweight="bold", color=INK); level = v
        else:
            bottom = level + min(v, 0)
            ax.bar(i, abs(v), bottom=bottom, color=c, width=0.6)
            ax.text(i, level + max(v, 0) + 2000, ("+" if v > 0 else "−") + eur(abs(v)), ha="center", fontsize=12, fontweight="bold", color=INK)
            level += v
    ax.set_xticks(range(4), [s[0] for s in steps], fontsize=11); ax.set_ylim(0, 200_000)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v / 1000:.0f} k€")
    rx = fig.add_axes([0.68, 0.15, 0.29, 0.62]); rx.axis("off"); rx.set_xlim(0, 1); rx.set_ylim(0, 1)
    for k, (big, small) in enumerate([("−37 %", "de coût"), ("1 665 → 658", "livraisons"), ("1 741 → 765", "camions"), ("54 → 25", "camions le jour le plus chargé")]):
        rx.text(0, 0.95 - k * 0.24, big, fontsize=22, fontweight="bold", color=INK, va="top")
        rx.text(0, 0.86 - k * 0.24, small, fontsize=12, color=BODY, va="top")
    note(fig, "L'étape 3 ne change pas le coût du plan : elle le mesure exactement, camion par camion. La situation actuelle est mesurée de la même façon.")
    pdf.savefig(fig); save_png(fig, 8); plt.close(fig)


def p_grid(pdf: PdfPages) -> None:
    fig = page("Où tombent les livraisons dans la grille de prix", "Résultat de l'optimisation",
               "Nombre de livraisons par case de la grille (distance × poids), même échelle de couleur.")
    vmax = 0
    mats = []
    for d, wcol in ((baseline, "weight"), (deliveries, "weight")):
        km = np.searchsorted(KM, d["distance"].to_numpy(float)); kg = np.searchsorted(KG, d[wcol].to_numpy(float))
        m = pd.crosstab(km, kg).reindex(index=range(len(KM)), columns=range(len(KG)), fill_value=0)
        mats.append(m); vmax = max(vmax, m.to_numpy().max())
    rows = [r for r in range(len(KM)) if mats[0].loc[r].sum() + mats[1].loc[r].sum() > 0]
    for k, (m, title) in enumerate(zip(mats, ["aujourd'hui : 1 665 livraisons", "plan final : 658 livraisons"], strict=True)):
        ax = fig.add_axes([0.1, 0.47 - k * 0.33, 0.86, 0.26])
        a = m.loc[rows].to_numpy()
        ax.imshow(a, aspect="auto", cmap=SEQ, vmin=0, vmax=vmax)
        for i in range(a.shape[0]):
            for j in range(a.shape[1]):
                if a[i, j]:
                    ax.text(j, i, a[i, j], ha="center", va="center", fontsize=8, color="white" if a[i, j] > vmax * 0.55 else INK)
        ax.set_yticks(range(len(rows)), [f"≤ {KM[r]:g} km" for r in rows], fontsize=9)
        ax.set_xticks(range(len(KG)), [f"≤ {v:g}" if np.isfinite(v) else "> 4000" for v in KG], fontsize=8)
        ax.set_title(title, fontsize=12, color=INK, loc="left")
        for spine in ax.spines.values():
            spine.set_visible(False)
    fig.text(0.53, 0.09, "poids de la livraison (kg)", ha="center", fontsize=10, color=BODY)
    note(fig, "Les livraisons quittent les petites tranches, les plus chères au kilo, pour la colonne « > 4 000 kg » où le prix est forfaitaire.")
    pdf.savefig(fig); save_png(fig, 9); plt.close(fig)


def save_png(fig: plt.Figure, n: int) -> None:
    if PNG_DIR:
        fig.savefig(PNG_DIR / f"page{n:02d}.png", dpi=70)


if __name__ == "__main__":
    with PdfPages(OUT) as pdf:
        for build in (p_overview, p_decisions, p_cost, p_convergence, p_profiles, p_smoothing, p_loading, p_waterfall, p_grid):
            build(pdf)
        meta = pdf.infodict(); meta["Title"] = "Le moteur d'optimisation des livraisons"; meta["Subject"] = "Milestone 3"
    log.info("PDF written: %s", OUT)
