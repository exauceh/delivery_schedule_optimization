"""Build the illustrated planning presentation from the published CSV snapshot."""
import argparse
from collections import Counter
import csv
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.colors import HexColor, Color, white
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas


WIDTH, HEIGHT = 960, 540
INK, BLUE, TEAL = (HexColor(code) for code in ("#10243d", "#164b91", "#087b72"))
MUTED, PALE, BORDER = (HexColor(code) for code in ("#465d75", "#edf3fa", "#dce6f0"))
BASE = "strict_budget_main_pic_global"
STRICT = "rg05_rg06_moins_cher"


def rows(path):
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def money(value):
    return f"{float(value):,.2f}".replace(",", " ").replace(".", ",") + " €"


def build(snapshot, destination):
    kpis = {row["scenario"]: row for row in rows(snapshot / "comparatif.csv")}
    day_loads = {name: Counter() for name in ("main", BASE, STRICT)}
    for row in rows(snapshot / "calendrier.csv"):
        if row["scenario"] in day_loads:
            day_loads[row["scenario"]][date.fromisoformat(row["date"])] = int(row["trucks"])
    previous = {row["scenario"]: row for row in rows(snapshot.parent / "ensemble-final" / "comparatif.csv")}
    scores = rows(snapshot / "mesures.csv")
    measured = next(row for row in scores if row["mode"] == "80 couples")
    full = next(row for row in scores if row["mode"] == "180 couples")
    strict = kpis[STRICT]
    baseline = float(measured["reference_eur"])
    gap = float(strict["cost_eur"]) - float(kpis["main"]["cost_eur"])
    savings = baseline - float(strict["cost_eur"])

    pdf = canvas.Canvas(str(destination), pagesize=(WIDTH, HEIGHT), pageCompression=1, invariant=1)
    pdf.setTitle("Planification des livraisons — du graphe au plan final")
    pdf.setAuthor("Équipe Delivery Schedule Optimization")
    page = 0

    def text(x, y, value, size=15, color=INK, bold=False):
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        pdf.setFillColor(color)
        pdf.drawString(x, y, str(value))

    def block(x, y, value, max_width, size=15, leading=21, color=MUTED):
        for line in simpleSplit(value, "Helvetica", size, max_width):
            text(x, y, line, size, color)
            y -= leading
        return y

    def panel(x, y, w, h, fill=PALE):
        pdf.setFillColor(fill)
        pdf.roundRect(x, y, w, h, 12, stroke=0, fill=1)

    def arrow(x, end, y):
        pdf.setStrokeColor(BLUE)
        pdf.setLineWidth(1.6)
        pdf.line(x, y, end, y)
        pdf.line(end - 7, y + 4, end, y)
        pdf.line(end - 7, y - 4, end, y)

    def header(section, title, sub):
        nonlocal page
        page += 1
        pdf.setFillColor(white)
        pdf.rect(0, 0, WIDTH, HEIGHT, stroke=0, fill=1)
        pdf.setFillColor(TEAL)
        pdf.rect(0, HEIGHT - 7, WIDTH, 7, stroke=0, fill=1)
        text(45, 497, section.upper(), 11, TEAL, True)
        text(45, 454, title, 32, INK, True)
        block(45, 424, sub, 865, 15, 18)
        pdf.setStrokeColor(BORDER)
        pdf.line(45, 48, 915, 48)
        text(45, 29, "Consolidation des livraisons   |   Fournisseur × usine   |   Testbed prime", 10, MUTED)
        text(894, 29, f"{page:02d}", 10, TEAL, True)

    header("Résultat", "Du graphe au plan de livraison",
           "Un calendrier par couple, une règle de chargement commune et un choix coordonné sur les quatre usines.")
    cards = [(strict["peak"], "camions au jour de pointe"),
             (strict["trucks"], "camions sur la période"),
             (f'{float(measured["seconds"]):.2f}'.replace(".", ",") + " s", "du calcul aux exports")]
    for i, (value, label) in enumerate(cards):
        x = 45 + 292 * i
        panel(x, 244, 278, 123)
        text(x + 19, 298, value, 36, BLUE, True)
        text(x + 19, 269, label, 15)
    text(45, 198, f"{money(savings)} d’économies calculées", 25, TEAL, True)
    block(45, 164, "Le plan à 49 applique RG05 et RG06 aux 4 389 lignes préparées de main. "
          "La référence reconstruite compte 1 741 camions. Le coût de référence est calculé avec la grille tarifaire.",
          805, 17, 24)
    pdf.showPage()

    header("Règles", "Ce qu’un départ peut emporter",
           "Exemple de lecture lundi–jeudi. Le jour du besoin reste la dernière date possible.")
    for i, (label, detail) in enumerate((
        ("Lundi", "Départ autorisé"),
        ("Mardi et mercredi", "Besoins couverts par le départ du lundi"),
        ("Jeudi", "Nouveau départ. Ses besoins partent jeudi."),
    )):
        x = 45 + i * 292
        panel(x, 264, 275, 109)
        text(x + 15, 335, label, 19, TEAL, True)
        block(x + 15, 304, detail, 235)
        if i < 2:
            arrow(x + 275, x + 290, 316)
    text(45, 222, "RG06   Couvrir l’intervalle jusqu’au départ suivant, exclu", 18, INK, True)
    text(45, 184, "RG05   Utiliser tous les jours du profil ensemble au moins une semaine", 18, INK, True)
    block(45, 146, "Les écarts de date restent entre zéro et sept jours calendaires. Les réceptions ont lieu les jours "
          "ouvrés allemands. Un camion porte les lignes entières d’un seul couple et reste sous 24 500 kg et 110,4 m³.",
          855, 15, 22)
    pdf.showPage()

    header("Construction", "Trois méthodes proposent des calendriers",
           "Le graphe donne des départs compatibles avec RG06. Le chargeur et le tarif sont communs à toutes les propositions.")
    for i, (title, desc) in enumerate((
        ("Solveur de main", "Répartit les besoins dans leurs fenêtres, en tenant compte des jours fixes."),
        ("Heuristique de prime", "Construit des plans puis essaie de déplacer les lignes pour réduire le coût."),
        ("Graphe de Wissam", "Une arête relie deux départs successifs. Son coût vient des camions chargés sur le premier départ."),
    )):
        x = 45 + i * 292
        panel(x, 250, 278, 125)
        text(x + 14, 342, title, 18, BLUE, True)
        block(x + 14, 313, desc, 248, 14, 19)
    text(45, 204, "Jusqu’à 31 profils sur les 80 couples les plus coûteux", 20, INK, True)
    block(45, 171, "Pour les autres couples, le moteur conserve les profils observés. Il cherche aussi des calendriers "
          "qui évitent les trois dates les plus chargées. Le chargement glouton fournit un coût par camion. "
          "Le graphe est exact pour les coûts d’arête qu’on lui donne.", 858, 15, 22)
    pdf.showPage()

    header("Sélection", "Le lissage se décide au niveau du calendrier",
           "Choisir un candidat par couple. Additionner les camions de chaque date, puis comparer les pics globaux.")
    for i, (title, detail) in enumerate((
        ("1. Filtrer", "Écarter les calendriers qui enfreignent RG05 ou RG06."),
        ("2. Chiffrer", "Calculer le coût de transport camion par camion."),
        ("3. Combiner", "Chercher le plus petit pic sous le plafond de coût."),
        ("4. Exporter", "Vérifier la couverture et les capacités. Exporter les profils et les affectations."),
    )):
        x = 45 + i * 219
        panel(x, 233, 208, 140)
        text(x + 14, 337, title, 18, BLUE, True)
        block(x + 14, 308, detail, 177, 14, 19)
    text(45, 190, "Le plafond de main reste hors d’atteinte avec les deux règles", 19, INK, True)
    block(45, 160, f"Le meilleur coût trouvé pour RG05 et RG06 est {money(strict['cost_eur'])}. "
          f"Il dépasse de {money(gap)} le plafond de {money(kpis['main']['cost_eur'])}. "
          "Le moteur garde ce coût et choisit alors le calendrier au pic le plus bas parmi ses candidats.", 858, 15, 22)
    pdf.showPage()

    header("Calendrier", "Trois plans, une seule échelle de couleur",
           "Chaque case compte les camions du jour ouvré. Un contour entoure le maximum de chaque plan.")
    all_days = sorted({day for loads in day_loads.values() for day in loads})
    first = all_days[0] - timedelta(days=all_days[0].weekday())
    weeks = [first + timedelta(weeks=i) for i in range((all_days[-1] - first).days // 7 + 1)]
    maximum = max(max(loads.values()) for loads in day_loads.values())
    for index, (name, label) in enumerate((
        ("main", "main publié"), (BASE, "RG06 sous budget"), (STRICT, "RG05 et RG06 ciblé"),
    )):
        x = 105 + 275 * index
        counts = day_loads[name]
        text(x, 379, label, 16, INK, True)
        for j, weekday in enumerate(("Lun", "Mar", "Mer", "Jeu", "Ven")):
            text(x + j * 49 + 8, 355, weekday, 11, MUTED)
        for i, week in enumerate(weeks):
            y = 321 - 30 * i
            if index == 0:
                text(48, y + 7, week.strftime("%d/%m"), 11, MUTED)
            for j in range(5):
                val = counts[week + timedelta(days=j)]
                r = val / maximum
                fill = Color(.94 - .83*r, .97 - .70*r, .99 - .51*r)
                pdf.setFillColor(fill)
                pdf.setStrokeColor(INK)
                pdf.roundRect(x + j * 49, y, 45, 24, 4, stroke=int(val == max(counts.values())), fill=1)
                text(x + j * 49 + 13, y + 7, val, 11, white if r >= .61 else INK, True)
        text(x, 88, f'{sum(counts.values())} camions   |   pic {max(counts.values())}/jour', 13, BLUE, True)
    pdf.showPage()

    header("Arbitrage", "49 au pic dans le délai mesuré",
           "Comparer RG05 et RG06 sur les mêmes données, puis choisir le niveau de recherche adapté.")
    xcols = (58, 417, 526, 650, 810)
    for x, label in zip(xcols, ("Scénario", "Camions", "Pic/jour", "Coût", "Durée")):
        text(x, 375, label, 13, MUTED, True)
    variants = (
        ("Plan RG05 + RG06 précédent", previous[STRICT], "—"),
        ("Ciblage des 80 couples", strict, measured["seconds"] + " s"),
        ("Exploration des 180 couples", full, full["seconds"] + " s"),
    )
    for i, (label, result, timing) in enumerate(variants):
        y = 326 - i * 47
        panel(45, y - 12, 870, 37, PALE if i != 1 else HexColor("#dff4f0"))
        for x, value in zip(xcols, (label, result["trucks"], result["peak"], money(result["cost_eur"]), timing.replace(".", ","))):
            text(x, y, value, 14, INK, i == 1)
    text(45, 171, "Ciblage seul : de 52 à 49 camions au pic, au même coût.", 19, TEAL, True)
    block(45, 137, "Les 85,03 s mesurent un run local depuis la lecture des données jusqu’aux exports. "
          "Les calendriers publiés de main et de Wissam sont réutilisés. Les 4 389 lignes incluent les imputations "
          "de main, repérables dans les exports. La préférence de fréquence RG07 se traite séparément.", 853, 14, 20)
    pdf.showPage()
    pdf.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    parser.add_argument("--snapshot", type=Path, default=here / "ensemble-cible")
    parser.add_argument("--output", type=Path, default=here / "presentation-ensemble.pdf")
    args = parser.parse_args()
    build(args.snapshot, args.output)
