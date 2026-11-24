"""Lance le moteur pour l'API Gilbert & Co, dans un processus séparé.

L'API (api/main.py) écrit les réglages dans un fichier JSON, puis appelle :
    python engine_runner.py <reglages.json>
Le moteur tourne ici, avec ses processus parallèles, à l'abri du serveur web (indispensable sous Windows).
À la fin, ce script écrit resume.json dans le dossier de sortie : contrôles, objectifs, lissage, journal, temps.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from fusion.config import Config
from fusion.engine import run
from fusion.report import write


def _records(df) -> list[dict]:
    return json.loads(df.to_json(orient="records", force_ascii=False, date_format="iso"))


def main(settings_path: str) -> int:
    s = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    out = Path(s["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    try:
        kw = dict(
            rate_sheet_csv=Path(s["rate_sheet_csv"]),
            output_dir=out,
            anticipation_days=int(s["anticipation_days"]),
            deliver_before_first_need=bool(s["deliver_before_first_need"]),
            smoothing_max_extra_cost=float(s["smoothing_max_extra_cost"]),
            frequencies=tuple(range(1, int(s["max_frequency"]) + 1)),
            m2_baseline_eur=s.get("m2_baseline_eur"),
        )
        if s.get("source") == "milestone1":   # runs made before the imports existed
            kw["consolidated_csv"] = Path(s["consolidated_csv"])
        else:
            kw.update(consolidated_csv=None, schedule_csv=Path(s["schedule_csv"]), distances_csv=Path(s["distances_csv"]),
                      missing_load=s.get("missing_load", "full_truck"), missing_weight=s.get("missing_weight"),
                      missing_volume=s.get("missing_volume"), missing_distance=s.get("missing_distance", "max"))
        res = run(Config(**kw))
        tables = write(res)
        resume = {
            "ok": True,
            "runtime_s": res["runtime"],
            "controles": _records(tables["controles"]),
            "objectifs": _records(tables["objectifs"]),
            "lissage": _records(tables["lissage"]),
            "journal": {k: str(v) for k, v in res["log"].items()},
            "avant_lissage": res["before_smoothing"],
        }
    except Exception as exc:  # the interface shows the message instead of a crash
        resume = {"ok": False, "erreur": f"{type(exc).__name__} : {exc}", "trace": traceback.format_exc()}
    plain = lambda o: o.item() if hasattr(o, "item") else str(o)  # numpy numbers -> JSON
    (out / "resume.json").write_text(json.dumps(resume, ensure_ascii=False, indent=1, default=plain), encoding="utf-8")
    return 0 if resume["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
