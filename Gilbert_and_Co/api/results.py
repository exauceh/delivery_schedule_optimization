"""Lecture des sorties du moteur et préparation des données affichées par l'interface."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

WEEKDAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]


@lru_cache(maxsize=16)
def load(out_dir: str) -> dict:
    """The three outputs of one finished run (cached: a finished run never changes)."""
    out = Path(out_dir)
    o1 = pd.read_csv(out / "sortie1_profils.csv")
    o2 = pd.read_csv(out / "sortie2_camions.csv", parse_dates=["date_livraison", "date_besoin"])
    o3 = pd.read_csv(out / "sortie3_saving.csv")
    o1["freq_apres"] = o1["frequence_cible"].str.extract(r"(\d)").astype(int)
    o1["freq_avant_lissage"] = o1["jours_autorises_avant_lissage"].str.count(",") + 1
    trucks = o2.drop_duplicates("camion_id").copy()
    per_truck = o2.groupby("camion_id").agg(lignes=("line_id", "size"), articles=("material", "nunique"),
                                            besoins=("date_besoin", "nunique"))
    trucks = trucks.join(per_truck, on="camion_id")
    return dict(o1=o1, o2=o2, o3=o3, trucks=trucks)


def summary(out_dir: Path) -> dict:
    """Everything the overview, calendar, profiles, suppliers and controls views need, in one JSON."""
    d = load(str(out_dir))
    o1, o2, o3, trucks = d["o1"], d["o2"], d["o3"], d["trucks"]
    resume = json.loads((out_dir / "resume.json").read_text(encoding="utf-8"))

    plants = sorted(trucks["plant"].unique())
    used = trucks["date_livraison"].dt.date
    days = [d.date() for d in pd.bdate_range(used.min(), used.max())]  # every working day, so the weeks line up
    grid = trucks.groupby(["plant", trucks["date_livraison"].dt.date]).size()
    calendar = [[int(grid.get((p, day), 0)) for day in days] for p in plants]

    cost_before, cost_after = float(o3["cout_avant_eur"].sum()), float(o3["cout_apres_eur"].sum())
    by_plant = o3.groupby("plant").agg(avant=("cout_avant_eur", "sum"), apres=("cout_apres_eur", "sum"),
                                       camions_avant=("camions_avant", "sum"), camions_apres=("camions_apres", "sum"))
    profiles_before = o1["profil_avant"].value_counts().to_dict()
    profiles_after = o1["frequence_cible"].value_counts().to_dict()
    labels = sorted(set(profiles_before) | set(profiles_after), key=lambda s: ("mois" in s, int(s[0])))
    top = o3.nlargest(15, "saving_eur")

    return {
        "kpi": {
            "cost_before": cost_before, "cost_after": cost_after, "saving": cost_before - cost_after,
            "saving_pct": 100 * (cost_before - cost_after) / cost_before,
            "trucks_before": int(o3["camions_avant"].sum()), "trucks_after": int(len(trucks)),
            "deliveries_before": int(o1["livraisons_avant"].sum()), "deliveries_after": int(o1["livraisons_apres"].sum()),
            "couples": int(len(o1)), "lines": int(o2["line_id"].nunique()),
            "runtime_s": resume.get("runtime_s"),
            "imputed_trucks": int(o2.loc[o2["donnee_manquante"], "camion_id"].nunique()),
            "frequency_changes": int((o1["freq_apres"] != o1["freq_avant_lissage"]).sum()),
            "weekday_changes": int((o1["jours_autorises"] != o1["jours_autorises_avant_lissage"]).sum()),
            "fill_mean_pct": float(trucks[["remplissage_poids_pct", "remplissage_volume_pct"]].max(axis=1).mean()),   # per truck, the binding dimension
        },
        "objectives": resume["objectifs"],
        "controls": resume["controles"],
        "smoothing": resume["lissage"],
        "journal": resume["journal"],
        "plants": [{"plant": p, "avant": float(r.avant), "apres": float(r.apres), "camions_avant": int(r.camions_avant),
                    "camions_apres": int(r.camions_apres), "pic": int(max(row))}
                   for (p, r), row in zip(by_plant.iterrows(), calendar)],
        "calendar": {"plants": plants, "days": [day.isoformat() for day in days], "trucks": calendar},
        "anticipation": [{"jours": int(k), "lignes": int(v)} for k, v in o2["anticipation_j"].value_counts().sort_index().items()],
        "profiles": [{"profil": lab, "avant": int(profiles_before.get(lab, 0)), "apres": int(profiles_after.get(lab, 0))}
                     for lab in labels],
        "top_savings": [{"fournisseur": r.supplier_id, "usine": r.plant, "saving": float(r.saving_eur),
                         "pct": float(r.saving_pct), "avant": float(r.cout_avant_eur), "apres": float(r.cout_apres_eur)}
                        for r in top.itertuples()],
        "filters": {"plants": plants, "suppliers": sorted(trucks["supplier_id"].unique()),
                    "days": [day.isoformat() for day in days]},
    }


def trucks_table(out_dir: Path, plant: str | None, supplier: str | None, day: str | None,
                 limit: int, offset: int) -> dict:
    t = load(str(out_dir))["trucks"]
    if plant:
        t = t[t["plant"] == plant]
    if supplier:
        t = t[t["supplier_id"] == supplier]
    if day:
        t = t[t["date_livraison"].dt.date.astype(str) == day]
    page = t.iloc[offset:offset + limit]
    rows = [{"id": r.camion_id, "fournisseur": r.supplier_id, "usine": r.plant, "date": r.date_livraison.date().isoformat(),
             "jour": r.jour, "lignes": int(r.lignes), "articles": int(r.articles), "kg": float(r.poids_camion),
             "m3": float(r.volume_camion), "remplissage": float(r.remplissage_poids_pct), "prix": float(r.prix_camion_eur),
             "remplace": int(r.besoins)} for r in page.itertuples()]
    return {"total": int(len(t)), "rows": rows}


def truck_detail(out_dir: Path, truck_id: str) -> dict | None:
    d = load(str(out_dir))
    lines = d["o2"][d["o2"]["camion_id"] == truck_id]
    if lines.empty:
        return None
    r = d["trucks"][d["trucks"]["camion_id"] == truck_id].iloc[0]
    articles = (lines.groupby(["material", "unloading_point"])
                .agg(besoins=("date_besoin", lambda s: sorted({x.strftime("%d/%m") for x in s})),
                     quantite=("schedule_quantity", "sum"), kg=("weight", "sum"), m3=("volume", "sum"),
                     manquant=("donnee_manquante", "any"))
                .reset_index().sort_values("kg", ascending=False))
    return {
        "id": truck_id, "fournisseur": r.supplier_id, "usine": r.plant, "date": r.date_livraison.date().isoformat(),
        "jour": r.jour, "kg": float(r.poids_camion), "m3": float(r.volume_camion), "prix": float(r.prix_camion_eur),
        "remplissage_poids": float(r.remplissage_poids_pct), "remplissage_volume": float(r.remplissage_volume_pct),
        "remplace": int(lines["date_besoin"].nunique()),
        "articles": [{"article": a.material, "quai": a.unloading_point, "besoins": a.besoins, "quantite": float(a.quantite),
                      "kg": float(a.kg), "m3": float(a.m3), "manquant": bool(a.manquant)} for a in articles.itertuples()],
    }


def profiles_table(out_dir: Path, plant: str | None, query: str | None) -> list[dict]:
    o1 = load(str(out_dir))["o1"]
    if plant:
        o1 = o1[o1["plant"] == plant]
    if query:
        o1 = o1[o1["supplier_id"].str.contains(query.strip(), case=False, regex=False)]
    return [{"fournisseur": r.supplier_id, "usine": r.plant, "km": float(r.distance_km), "avant": r.profil_avant,
             "apres": r.frequence_cible, "jours": r.jours_autorises, "jours_avant_lissage": r.jours_autorises_avant_lissage,
             "frequence_changee": bool(r.freq_apres != r.freq_avant_lissage), "livraisons_avant": int(r.livraisons_avant),
             "livraisons_apres": int(r.livraisons_apres), "camions": int(r.camions_apres),
             "remplissage": float(r.remplissage_poids_moy_pct)} for r in o1.itertuples()]


def suppliers_table(out_dir: Path) -> list[dict]:
    o3 = load(str(out_dir))["o3"]
    return [{"fournisseur": r.supplier_id, "usine": r.plant, "avant": float(r.cout_avant_eur), "apres": float(r.cout_apres_eur),
             "camions_avant": int(r.camions_avant), "camions_apres": int(r.camions_apres), "saving": float(r.saving_eur),
             "pct": float(r.saving_pct)} for r in o3.itertuples()]
