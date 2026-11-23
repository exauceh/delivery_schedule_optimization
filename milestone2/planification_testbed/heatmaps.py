"""Regenerate the shared-scale calendar heatmaps from the exported truck plans."""
from collections import Counter
import argparse
import csv
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape


def render(root: Path, smoothing_calendar: Path | None = None, final_calendar: Path | None = None,
           output: Path | None = None) -> Path:
    panels = []
    if smoothing_calendar is None and final_calendar is None:
        exports = root / "results" / "fast_plan"
        for key, label in (("A", "Référence reconstruite"), ("B", "Fournisseur × usine"), ("C", "Fournisseur → hub")):
            with (exports / f"camions_{key}.csv").open(newline="", encoding="utf-8") as file:
                counts = Counter(date.fromisoformat(row["date"]) for row in csv.DictReader(file))
            panels.append((key, label, counts))
        subtitle = "Même périmètre mesuré et tarifable"
        filename = "heatmap-camions.svg"
    else:
        calendar_path = final_calendar or smoothing_calendar
        with calendar_path.open(newline="", encoding="utf-8") as file:
            calendar = list(csv.DictReader(file))
        if final_calendar is not None:
            full_rule = ("principal_rg05_rg06" if any(r["scenario"] == "principal_rg05_rg06" for r in calendar)
                         else "rg05_rg06_moins_cher")
            specs = (("A", "Moteur main", "main"), ("B", "RG06 sous budget", "strict_budget_main_pic_global"),
                     ("C", "RG05 et RG06", full_rule))
            filename = "heatmap-ensemble-final.svg"
        else:
            specs = (("A", "Moteur main", "main"), ("B", "Heuristique seule", "heuristique"),
                     ("C", "Plan mixte sans surcoût", "budget_0"))
            filename = "heatmap-lissage.svg"
        for key, label, scenario in specs:
            counts = Counter({date.fromisoformat(row["date"]): int(row["trucks"])
                              for row in calendar if row["scenario"] == scenario})
            panels.append((key, label, counts))
        subtitle = "Fournisseur × usine · mêmes 4 389 lignes préparées"
    days = sorted({day for _, _, counts in panels for day in counts})
    if any(day.weekday() > 4 for day in days):
        raise ValueError("A truck is scheduled on a weekend")
    first = days[0] - timedelta(days=days[0].weekday())
    weeks = [first + timedelta(weeks=index) for index in range((days[-1] - first).days // 7 + 1)]
    maximum = max(max(counts.values()) for _, _, counts in panels)
    height = 247 + len(weeks) * 43
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1130" height="{height}" viewBox="0 0 1130 {height}" role="img" aria-labelledby="title desc">',
           '<title id="title">Camions planifiés par jour</title>',
           '<desc id="desc">Les trois cartes utilisent un calendrier commun et une échelle commune, avec un compte par jour ouvré.</desc>',
           '<defs><linearGradient id="scale"><stop stop-color="#eef4f8"/><stop offset="1" stop-color="#103d72"/></linearGradient></defs>',
           f'<rect width="1130" height="{height}" fill="#fff"/>']

    def label(x, y, value, size=14, fill="#142c44", weight=400, anchor="start"):
        svg.append(f'<text x="{x}" y="{y}" font-family="Segoe UI, Arial, sans-serif" font-size="{size}" fill="{fill}" font-weight="{weight}" text-anchor="{anchor}">{escape(str(value))}</text>')

    label(26, 44, "Camions planifiés par jour", 28, weight=700)
    label(26, 70, f"{subtitle} · semaines commençant le lundi · {days[0].year}", 15, "#405a70")
    for idx, (key, name, counts) in enumerate(panels):
        x = 109 + idx * 332
        label(x, 110, f"{key} · {name}", 18, weight=700)
        label(x, 136, f"{sum(counts.values()):,} camions · pic {max(counts.values())}/jour".replace(",", " "), 14, "#405a70")
        for weekday, name in enumerate(("Lun.", "Mar.", "Mer.", "Jeu.", "Ven.")):
            label(x + weekday * 61 + 28, 166, name, anchor="middle")
        for row, week in enumerate(weeks):
            y = 177 + row * 43
            if idx == 0:
                label(26, y + 24, week.strftime("%d/%m"))
            for weekday in range(5):
                day = week + timedelta(days=weekday)
                value = counts[day]
                scale = value / maximum
                rgb = tuple(round(a + scale * (b - a)) for a, b in zip((238, 244, 248), (16, 61, 114)))
                color = "#{:02x}{:02x}{:02x}".format(*rgb)
                bx = x + weekday * 61
                tip = f"{day:%d/%m/%Y} · {value} camion(s) · scénario {key}"
                border = ' stroke="#172f49" stroke-width="2"' if value == max(counts.values()) else ""
                svg.append(f'<rect x="{bx}" y="{y}" width="56" height="37" rx="5" fill="{color}"{border}><title>{tip}</title></rect>')
                label(bx + 28, y + 24, value, fill="#fff" if scale >= 0.62 else "#142c44", weight=600, anchor="middle")
    legend_y = 196 + len(weeks) * 43
    label(26, legend_y + 11, "Camions par jour")
    svg.append(f'<rect x="159" y="{legend_y}" width="170" height="13" rx="3" fill="url(#scale)"/>')
    label(159, legend_y + 32, 0)
    label(329, legend_y + 32, maximum, anchor="end")
    label(390, legend_y + 11, "Échelle commune · le contour indique le pic de chaque scénario.", fill="#405a70")
    svg.append("</svg>")
    destination = output or Path(__file__).with_name(filename)
    destination.write_text("\n".join(svg), encoding="utf-8")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--lissage", type=Path, metavar="CALENDRIER_CSV")
    mode.add_argument("--ensemble", type=Path, metavar="CALENDRIER_CSV")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(render(Path(__file__).resolve().parents[2], args.lissage, args.ensemble, args.output))
