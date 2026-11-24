"""Rule controls (is the plan correct?), teacher's targets (is it good enough?), summary and files."""
from __future__ import annotations

import pandas as pd

from .data import Calendar
from .engine import DAYS


def controls(res: dict) -> pd.DataFrame:
    cfg, cal, needs, o1, o2 = res["cfg"], res["cal"], res["needs"], res["o1"], res["o2"]
    prof = o1.set_index(["supplier_id", "plant"])
    dl = o2.drop_duplicates(["supplier_id", "plant", "date_livraison"]).copy()
    dl["wk"] = [Calendar.week_key(x) for x in dl.date_livraison]
    per_week = dl.groupby(["supplier_id", "plant", "wk"]).size()
    allowed = {k: {DAYS.index(j) for j in v.split(", ")} for k, v in prof["jours_autorises"].items()}
    trucks = o2.drop_duplicates("camion_id")
    rule24 = all(any(set(g2.date_livraison.map(lambda x: x.weekday())) >= allowed[k] for w, g2 in g.groupby("wk")
                     if w in cal.full_weeks) for k, g in dl.groupby(["supplier_id", "plant"]))
    rows = [
        ("Règle 10", "Chaque besoin livré une seule fois", o2.line_id.is_unique and set(o2.line_id) == set(needs.line_id)),
        ("Règle 11", "Jamais de retard", (o2.anticipation_j >= 0).all()),
        ("Règle 12", f"Anticipation ≤ {cfg.anticipation_days} jours", (o2.anticipation_j <= cfg.anticipation_days).all()),
        ("Règles 7-9", "Livraison un jour ouvré du calendrier", o2.date_livraison.isin(set(cal.open_days)).all()),
        ("Règle 13", "Capacité poids et volume", (trucks.poids_camion <= cfg.truck_kg + 1e-6).all() and (trucks.volume_camion <= cfg.truck_m3 + 1e-6).all()),
        ("Règle 14", "Un camion = un couple", (o2.groupby("camion_id")[["supplier_id", "plant"]].nunique() == 1).all().all()),
        ("Règle 16", "Ligne jamais coupée", o2.line_id.is_unique),
        ("Conservation", "Poids et volume totaux identiques", abs(o2.weight.sum() - needs.weight.sum()) < 1e-6 and abs(o2.volume.sum() - needs.volume.sum()) < 1e-6),
        ("Règle 23", "Livraison uniquement les jours autorisés", all(x.date_livraison.weekday() in allowed[x.supplier_id, x.plant] for x in dl.itertuples())),
        ("Règle 24", "Profil atteint sur une semaine complète", rule24),
        ("Règle 25", "Livraisons par semaine ≤ profil", all(n <= len(allowed[s, p]) for (s, p, _), n in per_week.items())),
        ("Règle 28", f"Surcoût du lissage ≤ {100 * cfg.smoothing_max_extra_cost:g} % par usine", all(s["surcout_pct"] <= 100 * cfg.smoothing_max_extra_cost + 1e-9 for s in res["smooth_log"])),
        ("Règle 29", "Le lissage n'ajoute aucun camion", trucks.shape[0] <= res["before_smoothing"]["trucks"]),
    ]
    return pd.DataFrame(rows, columns=["regle", "controle", "ok"])


def targets(res: dict) -> pd.DataFrame:
    cfg, o3 = res["cfg"], res["o3"]
    saving, n = o3.cout_avant_eur.sum() - o3.cout_apres_eur.sum(), res["o2"].camion_id.nunique()
    return pd.DataFrame([
        ("Temps d'exécution", f"{res['runtime']:.1f} s (dépend de la machine)", f"< {cfg.target_seconds:.0f} s", res["runtime"] < cfg.target_seconds),
        ("Saving", f"{saving:,.0f} €".replace(",", " "), f"≥ {cfg.target_saving_eur:,.0f} €".replace(",", " "), saving >= cfg.target_saving_eur),
        ("Camions", str(n), f"< {cfg.target_max_trucks}", n < cfg.target_max_trucks),
    ], columns=["objectif", "valeur", "cible", "atteint"])


def summary(res: dict) -> pd.DataFrame:
    o1, o2, o3, b = res["o1"], res["o2"], res["o3"], res["before_smoothing"]
    cal = o2.drop_duplicates("camion_id").pivot_table(index="date_livraison", columns="plant", values="camion_id", aggfunc="count", fill_value=0)
    before, after = o3.cout_avant_eur.sum(), o3.cout_apres_eur.sum()
    return pd.DataFrame([
        ("Couples fournisseur × usine", len(o1)),
        ("Lignes de besoin retenues", len(res["needs"])),
        ("Livraisons avant / avant lissage / après", f"{o1.livraisons_avant.sum()} / {b['deliveries']} / {o1.livraisons_apres.sum()}"),
        ("Camions avant / avant lissage / après", f"{o3.camions_avant.sum()} / {b['trucks']} / {o2.camion_id.nunique()}"),
        ("dont camions pleins imposés par des données manquantes", o2[o2.donnee_manquante].camion_id.nunique()),
        ("Coût avant (EUR)", round(before, 2)),
        ("Coût avant lissage (EUR)", round(b["cost"], 2)),
        ("Coût après (EUR)", round(after, 2)),
        ("Saving (EUR)", round(before - after, 2)),
        ("Saving (%)", round(100 * (before - after) / before, 2)),
        ("Pics par usine (camions / jour)", " / ".join(f"{c} {int(cal[c].max())}" for c in sorted(cal.columns))),
        ("Couples dont le profil change au lissage", int(o1.profil_change_au_lissage.sum())),
        ("Remplissage poids moyen après (%)", round(100 * o2.drop_duplicates("camion_id").poids_camion.mean() / res["cfg"].truck_kg, 1)),
        ("Temps d'exécution (s)", round(res["runtime"], 1)),
    ], columns=["indicateur", "valeur"]), cal


def write(res: dict) -> dict:
    out = res["cfg"].output_dir
    out.mkdir(parents=True, exist_ok=True)
    synth, cal = summary(res)
    ctrl, tgt = controls(res), targets(res)
    res["o1"].to_csv(out / "sortie1_profils.csv", index=False)
    res["o2"].to_csv(out / "sortie2_camions.csv", index=False)
    res["o3"].to_csv(out / "sortie3_saving.csv", index=False)
    with pd.ExcelWriter(out / "resultats_consolidation.xlsx") as xw:
        synth.to_excel(xw, sheet_name="Synthese", index=False)
        res["o1"].to_excel(xw, sheet_name="Sortie1_Profils", index=False)
        res["o2"].to_excel(xw, sheet_name="Sortie2_Camions", index=False)
        res["o3"].to_excel(xw, sheet_name="Sortie3_Saving", index=False)
        pd.DataFrame(res["smooth_log"]).to_excel(xw, sheet_name="Lissage", index=False)
        cal.to_excel(xw, sheet_name="Camions_par_jour")
        pd.DataFrame(list(res["log"].items()), columns=["etape", "valeur"]).to_excel(xw, sheet_name="Preparation", index=False)
        ctrl.to_excel(xw, sheet_name="Controles", index=False)
        tgt.to_excel(xw, sheet_name="Objectifs", index=False)
    return dict(synthese=synth, controles=ctrl, objectifs=tgt, lissage=pd.DataFrame(res["smooth_log"]))
