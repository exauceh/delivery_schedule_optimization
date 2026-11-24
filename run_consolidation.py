#!/usr/bin/env python3
"""Usage : python run_consolidation.py <consolidated_dataset.csv (Milestone 1)> <rate_sheet.csv> [dossier_sortie]"""
import sys
from pathlib import Path

import pandas as pd

from fusion.config import Config
from fusion.engine import run
from fusion.report import write

if __name__ == "__main__":
    a = sys.argv[1:]
    cfg = Config(consolidated_csv=Path(a[0]), rate_sheet_csv=Path(a[1]),
                 output_dir=Path(a[2]) if len(a) > 2 else Path("sorties"))
    tables = write(run(cfg))
    pd.set_option("display.width", 200)
    for name, t in tables.items():
        print(f"\n== {name}\n{t.to_string(index=False)}")
    if not tables["controles"]["ok"].all():
        raise SystemExit("ÉCHEC : une règle n'est pas respectée")
