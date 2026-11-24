#!/usr/bin/env python3
"""Gilbert & Co : point d'entrée unique du projet.

    python main.py                 ouvre l'application (interface web + API) sur http://localhost:8000
    python main.py app --port 8080 idem, sur un autre port, sans ouvrir le navigateur avec --no-browser
    python main.py run             flux complet en ligne de commande sur l'exemple du projet
    python main.py run --planning mon_planning.csv --distances distances.csv [--grille rate_sheet.csv]
                       [--sortie sorties] [--poids full_truck|max|median|drop] [--volume ...] [--distance max|median|drop]
                       [--lissage 0.01] [--anticipation 7] [--depart-au-premier-besoin]

Le flux principal (commande « run ») enchaîne les étapes du projet :
  1. préparation des données brutes (règles 1 à 9, traitement des valeurs manquantes)  -> fusion.data
  2. contrôle : registre des anomalies et situation actuelle (coût sans regroupement)    -> api.situation
  3. optimisation : plans par couple, lissage, chargement exact                           -> fusion.engine
  4. écriture des sorties (CSV, Excel) et contrôle de chaque règle sur le plan final      -> fusion.report
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"


def run_pipeline(args: argparse.Namespace) -> int:
    import shutil
    import tempfile

    import pandas as pd

    from api.situation import analyse
    from fusion.config import Config
    from fusion.engine import run
    from fusion.report import write

    files = {"released_schedule.csv": args.planning, "distances.csv": args.distances, "rate_sheet.csv": args.grille}
    work = Path(tempfile.mkdtemp(prefix="gilbert_"))
    for name, src in files.items():
        shutil.copy(src, work / name)

    print("1-2. Données et situation actuelle")
    sit = analyse(str(work), args.poids, args.volume, args.distance)
    k = sit["kpi"]
    print(f"     {k['first']} -> {k['last']} · {k['lines']} lignes de besoin sur {k['raw_lines']} · "
          f"{k['deliveries']} livraisons · {k['trucks']} camions · {k['cost']:,.2f} €".replace(",", " "))
    for a in sit["anomalies"]:
        if a["count"]:
            print(f"     - {a['label']} : {a['count']} ({a['treatment']})")
    if sit["blocking"]:
        print("ÉCHEC : anomalie bloquante dans le planning, corrigez le fichier.")
        return 2

    print("3. Optimisation")
    cfg = Config(consolidated_csv=None, schedule_csv=work / "released_schedule.csv", distances_csv=work / "distances.csv",
                 rate_sheet_csv=work / "rate_sheet.csv", output_dir=Path(args.sortie), m2_baseline_eur=None,
                 missing_weight=args.poids, missing_volume=args.volume, missing_distance=args.distance,
                 anticipation_days=args.anticipation, deliver_before_first_need=not args.depart_au_premier_besoin,
                 smoothing_max_extra_cost=args.lissage)
    tables = write(run(cfg))

    print("4. Résultats")
    pd.set_option("display.width", 200)
    print(tables["objectifs"].to_string(index=False))
    ok = bool(tables["controles"]["ok"].all())
    print(f"     contrôles : {'tous respectés' if ok else 'ÉCHEC'} · sorties dans {Path(args.sortie).resolve()}")
    shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


def run_app(args: argparse.Namespace) -> int:
    import uvicorn

    url = f"http://localhost:{args.port}"
    if not args.no_browser:
        threading.Timer(2.5, lambda: webbrowser.open(url)).start()
    print(f"Gilbert & Co tourne sur {url} (Ctrl+C pour l'arrêter)")
    uvicorn.run("api.main:app", host=args.host, port=args.port, workers=1)
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Gilbert & Co : consolidation des livraisons fournisseurs")
    sub = p.add_subparsers(dest="cmd")
    a = sub.add_parser("app", help="ouvrir l'application web (par défaut)")
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=8000)
    a.add_argument("--no-browser", action="store_true")
    r = sub.add_parser("run", help="flux complet en ligne de commande")
    r.add_argument("--planning", type=Path, default=DOCS / "released_schedule.csv")
    r.add_argument("--distances", type=Path, default=DOCS / "distances.csv")
    r.add_argument("--grille", type=Path, default=DOCS / "rate_sheet.csv")
    r.add_argument("--sortie", default="sorties")
    r.add_argument("--poids", default="full_truck", choices=["full_truck", "max", "median", "drop"])
    r.add_argument("--volume", default="full_truck", choices=["full_truck", "max", "median", "drop"])
    r.add_argument("--distance", default="max", choices=["max", "median", "drop"])
    r.add_argument("--anticipation", type=int, default=7)
    r.add_argument("--lissage", type=float, default=0.0, help="surcoût accepté par usine pour le lissage, ex. 0.01")
    r.add_argument("--depart-au-premier-besoin", action="store_true", help="ne rien livrer avant le premier besoin")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.cmd == "run":
        return run_pipeline(args)
    if args.cmd is None:
        args = parser().parse_args(["app"])
    return run_app(args)


if __name__ == "__main__":
    sys.exit(main())
