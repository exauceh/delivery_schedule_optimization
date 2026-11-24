"""Gilbert & Co : l'API du moteur de consolidation des livraisons (FastAPI).

Lancer en local :  uvicorn api.main:app --port 8000   puis ouvrir http://localhost:8000
L'API sert aussi l'interface (dossier web/).
Parcours : un « import » (planning, distances, grille tarifaire) -> sa situation actuelle et son registre d'anomalies
-> un ou plusieurs « runs » d'optimisation sur cet import. Les imports sont rangés dans IMPORTS_DIR/<id>/, les runs dans
RUNS_DIR/<id>/. Les calculs passent un par un dans une file d'attente, car le moteur utilise déjà tous les cœurs.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import results, situation

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = Path(os.environ.get("GILBERT_RUNS_DIR", ROOT / "runs")).resolve()
IMPORTS_DIR = Path(os.environ.get("GILBERT_IMPORTS_DIR", RUNS_DIR.parent / "imports")).resolve()
RUN_TIMEOUT_S = int(os.environ.get("GILBERT_RUN_TIMEOUT_S", "900"))
MAX_UPLOAD_MB = int(os.environ.get("GILBERT_MAX_UPLOAD_MB", "50"))
DEFAULT_FILES = {   # the project's files: the example import, and the rate sheet when none is uploaded
    "rates": ROOT / "docs" / "rate_sheet.csv",
    "schedule": ROOT / "docs" / "released_schedule.csv",
    "distances": ROOT / "docs" / "distances.csv",
}
IMPORT_FILES = {"schedule": "released_schedule.csv", "distances": "distances.csv", "rates": "rate_sheet.csv"}
DOWNLOADS = {"resultats_consolidation.xlsx", "sortie1_profils.csv", "sortie2_camions.csv", "sortie3_saving.csv"}
RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")

RUNS_DIR.mkdir(parents=True, exist_ok=True)
IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="Gilbert & Co", version="1.0",
              description="Consolidation des livraisons fournisseurs : lancer un calcul, suivre son avancement, lire les résultats.")
queue = ThreadPoolExecutor(max_workers=1)  # one engine at a time: it already uses every core
state: dict[str, dict] = {}                 # live status of the runs of this server process
lock = threading.Lock()


TREAT = "^(full_truck|max|median|drop)$"


class Settings(BaseModel):
    label: str = Field("Scénario", max_length=60)
    import_id: str
    anticipation_days: int = Field(7, ge=0, le=21)
    deliver_before_first_need: bool = True     # agreed: deliveries may start 7 days before the first need
    smoothing_max_extra_cost: float = Field(0.0, ge=0, le=0.05)
    max_frequency: int = Field(5, ge=1, le=5)
    missing_weight: str = Field("full_truck", pattern=TREAT)
    missing_volume: str = Field("full_truck", pattern=TREAT)
    missing_distance: str = Field("max", pattern="^(max|median|drop)$")
    missing_load: str = "full_truck"            # kept for the older runs


SETTING_KEYS = ("import_id", "anticipation_days", "deliver_before_first_need", "smoothing_max_extra_cost", "max_frequency",
                "missing_weight", "missing_volume", "missing_distance")


# ----------------------------------------------------------------------------- imports
def _import_dir(import_id: str) -> Path:
    if not RUN_ID.match(import_id) or not (IMPORTS_DIR / import_id / "import.json").exists():
        raise HTTPException(404, "Import introuvable")
    return IMPORTS_DIR / import_id


def _import_meta(import_id: str) -> dict:
    d = IMPORTS_DIR / import_id
    meta = json.loads((d / "import.json").read_text(encoding="utf-8"))
    meta["runs"] = sum(1 for r in RUNS_DIR.iterdir() if (r / "reglages.json").exists()
                       and json.loads((r / "reglages.json").read_text(encoding="utf-8")).get("import_id") == import_id)
    return meta


@app.post("/api/imports", status_code=201, summary="Importer un planning, ses distances et une grille tarifaire")
async def create_import(schedule: UploadFile | None = File(None), distances: UploadFile | None = File(None),
                        rates: UploadFile | None = File(None), example: bool = Form(False), label: str = Form("")) -> dict:
    import_id = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    d = IMPORTS_DIR / import_id
    d.mkdir(parents=True)
    names = {}
    try:
        for key, upload in (("schedule", schedule), ("distances", distances), ("rates", rates)):
            path = None if example else await _save(upload, d, IMPORT_FILES[key])
            if path is None:
                if key != "rates" and not example:
                    raise HTTPException(400, {"schedule": "Le planning de livraison", "distances": "Le fichier des distances"}[key]
                                        + " est nécessaire.")
                (d / IMPORT_FILES[key]).write_bytes(DEFAULT_FILES[key].read_bytes())
                names[key] = f"{DEFAULT_FILES[key].name} (exemple du projet)" if example or key != "rates" else \
                             f"{DEFAULT_FILES[key].name} (grille du projet)"
            else:
                names[key] = upload.filename
            if key != "rates":
                try:
                    situation.check_columns(key, d / IMPORT_FILES[key])
                except Exception as exc:
                    raise HTTPException(400, f"{names[key]} : {exc}") from exc
        try:
            info = situation.describe(d / IMPORT_FILES["schedule"])
            situation.analyse(str(d))            # fails early if the rate sheet or the data cannot be read
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, f"Fichiers illisibles : {exc}") from exc
    except HTTPException:
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
        raise
    created = dt.datetime.now()
    meta = {"id": import_id, "created_at": created.isoformat(timespec="seconds"),
            "label": label.strip()[:60] or ("Exemple du projet" if example else Path(names["schedule"]).stem[:60]),
            "files": names, "example": example, **info}
    (d / "import.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return _import_meta(import_id)


@app.get("/api/imports", summary="Tous les imports, du plus récent au plus ancien")
def list_imports() -> list[dict]:
    return [_import_meta(d.name) for d in sorted(IMPORTS_DIR.iterdir(), reverse=True)
            if d.is_dir() and RUN_ID.match(d.name) and (d / "import.json").exists()]


@app.get("/api/imports/{import_id}", summary="Un import")
def get_import(import_id: str) -> dict:
    _import_dir(import_id)
    return _import_meta(import_id)


@app.get("/api/imports/{import_id}/situation", summary="Situation actuelle, anomalies et règles appliquées")
def get_situation(import_id: str, weight: str = "full_truck", volume: str = "full_truck", distance: str = "max") -> dict:
    d = _import_dir(import_id)
    if weight not in situation.TREATMENTS["weight"] or volume not in situation.TREATMENTS["volume"] \
            or distance not in situation.TREATMENTS["distance"]:
        raise HTTPException(422, "Traitement inconnu")
    try:
        return {"import": _import_meta(import_id), **situation.analyse(str(d), weight, volume, distance)}
    except Exception as exc:
        raise HTTPException(400, f"Données inutilisables avec ces traitements : {exc}") from exc


@app.delete("/api/imports/{import_id}", status_code=204, summary="Supprimer un import")
def delete_import(import_id: str) -> Response:
    d = _import_dir(import_id)
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
    situation.analyse.cache_clear()
    return Response(status_code=204)


# ----------------------------------------------------------------------------- runs
def _run_dir(run_id: str) -> Path:
    if not RUN_ID.match(run_id) or not (RUNS_DIR / run_id).is_dir():
        raise HTTPException(404, "Calcul introuvable")
    return RUNS_DIR / run_id


def _status(run_id: str) -> dict:
    d = RUNS_DIR / run_id
    settings = json.loads((d / "reglages.json").read_text(encoding="utf-8"))
    live = state.get(run_id, {})
    resume_path = d / "sorties" / "resume.json"
    if resume_path.exists():
        resume = json.loads(resume_path.read_text(encoding="utf-8"))
        status = "termine" if resume.get("ok") else "arrete" if resume.get("arrete") else "erreur"
        error = None if resume.get("ok") else resume.get("erreur")
    elif live.get("status") == "fini":  # the engine stopped without writing its summary
        status, error = "erreur", "le moteur s'est arrêté sans résultat (voir moteur.log)"
    else:
        status, error = live.get("status", "interrompu"), None
    started = live.get("started")
    return {"id": run_id, "label": settings["label"], "created_at": settings["created_at"], "status": status,
            "error": error, "elapsed_s": round(time.time() - started, 1) if started and status == "en_cours" else None,
            "import_label": settings.get("import_label"),
            "settings": {k: settings.get(k) for k in SETTING_KEYS}}


def _kill(proc: subprocess.Popen) -> None:
    """Stop the engine and the worker processes it started (process tree)."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _execute(run_id: str) -> None:
    d = RUNS_DIR / run_id
    with lock:
        if state[run_id].get("stop"):  # stopped while it was waiting in the queue
            state[run_id]["status"] = "fini"
            return
        state[run_id].update(status="en_cours", started=time.time())
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    with open(d / "moteur.log", "w", encoding="utf-8") as log:
        proc = subprocess.Popen([sys.executable, str(ROOT / "engine_runner.py"), str(d / "reglages.json")], cwd=ROOT,
                                stdout=log, stderr=subprocess.STDOUT, **group)
        with lock:
            state[run_id]["proc"] = proc
        try:
            proc.wait(timeout=RUN_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            _kill(proc)
            out = d / "sorties"
            out.mkdir(exist_ok=True)
            (out / "resume.json").write_text(json.dumps({"ok": False, "erreur": f"calcul arrêté après {RUN_TIMEOUT_S} s"}),
                                             encoding="utf-8")
    with lock:
        state[run_id]["status"] = "fini"
        state[run_id].pop("proc", None)


async def _save(upload: UploadFile | None, folder: Path, name: str) -> Path | None:
    if upload is None or not upload.filename:
        return None
    data = await upload.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"{upload.filename} dépasse {MAX_UPLOAD_MB} Mo")
    if not upload.filename.lower().endswith(".csv"):
        raise HTTPException(400, f"{upload.filename} : un fichier .csv est attendu")
    path = folder / name
    path.write_bytes(data)
    return path


@app.post("/api/runs", status_code=202, summary="Lancer une optimisation sur un import")
async def create_run(settings: str = Form("{}")) -> dict:
    try:
        s = Settings(**json.loads(settings))
    except Exception as exc:
        raise HTTPException(422, f"Réglages invalides : {exc}") from exc
    src = _import_dir(s.import_id)
    meta = _import_meta(s.import_id)
    try:
        sit = situation.analyse(str(src), s.missing_weight, s.missing_volume, s.missing_distance)
    except Exception as exc:
        raise HTTPException(400, f"Données inutilisables : {exc}") from exc
    if sit["blocking"]:
        bad = "; ".join(f"{a['label']} : {a['detail']}" for a in sit["anomalies"] if a["kind"] == "bloquant" and a["count"])
        raise HTTPException(400, f"Le planning contient une anomalie bloquante ({bad}). Corrigez le fichier puis importez-le à nouveau.")
    run_id = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    d = RUNS_DIR / run_id
    d.mkdir(parents=True)
    (d / "situation.json").write_text(json.dumps({"import": meta, **sit}, ensure_ascii=False), encoding="utf-8")
    payload = {**s.model_dump(), "source": "raw", "import_label": meta["label"],
               "created_at": dt.datetime.now().isoformat(timespec="seconds"), "output_dir": str(d / "sorties"),
               "rate_sheet_csv": str(src / IMPORT_FILES["rates"]), "schedule_csv": str(src / IMPORT_FILES["schedule"]),
               "distances_csv": str(src / IMPORT_FILES["distances"]), "consolidated_csv": None, "m2_baseline_eur": None}
    (d / "reglages.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    with lock:
        state[run_id] = {"status": "en_attente"}
    queue.submit(_execute, run_id)
    return _status(run_id)


@app.post("/api/runs/{run_id}/stop", summary="Arrêter un calcul en attente ou en cours")
def stop_run(run_id: str) -> dict:
    d = _run_dir(run_id)
    if _status(run_id)["status"] not in ("en_attente", "en_cours"):
        raise HTTPException(409, "Le calcul n'est pas en cours")
    out = d / "sorties"
    out.mkdir(exist_ok=True)
    (out / "resume.json").write_text(json.dumps({"ok": False, "arrete": True, "erreur": "calcul arrêté à la demande"},
                                                ensure_ascii=False), encoding="utf-8")
    with lock:
        live = state.setdefault(run_id, {})
        live["stop"] = True
        proc = live.get("proc")
    if proc is not None:
        _kill(proc)
    return _status(run_id)


@app.get("/api/runs", summary="Tous les calculs, du plus récent au plus ancien")
def list_runs() -> list[dict]:
    out = []
    for d in sorted(RUNS_DIR.iterdir(), reverse=True):
        if d.is_dir() and RUN_ID.match(d.name) and (d / "reglages.json").exists():
            st = _status(d.name)
            if st["status"] == "termine":
                sm = results.summary(d / "sorties")
                st["kpi"] = {key: sm["kpi"][key] for key in ("saving", "saving_pct", "trucks_after", "deliveries_after", "runtime_s")}
                st["peaks"] = [p["pic"] for p in sm["plants"]]
            out.append(st)
    return out


@app.get("/api/runs/{run_id}", summary="État d'un calcul")
def get_run(run_id: str) -> dict:
    _run_dir(run_id)
    return _status(run_id)


@app.delete("/api/runs/{run_id}", status_code=204, summary="Supprimer un calcul terminé")
def delete_run(run_id: str) -> Response:
    d = _run_dir(run_id)
    if _status(run_id)["status"] in ("en_attente", "en_cours"):
        raise HTTPException(409, "Calcul en cours")
    for f in sorted(d.rglob("*"), reverse=True):
        f.unlink() if f.is_file() else f.rmdir()
    d.rmdir()
    results.load.cache_clear()
    return Response(status_code=204)


def _finished(run_id: str) -> Path:
    d = _run_dir(run_id)
    st = _status(run_id)
    if st["status"] != "termine":
        raise HTTPException(409, f"Le calcul n'est pas terminé ({st['status']})")
    return d / "sorties"


@app.get("/api/runs/{run_id}/summary", summary="Indicateurs, calendrier, profils, contrôles")
def get_summary(run_id: str) -> dict:
    return results.summary(_finished(run_id))


@app.get("/api/runs/{run_id}/situation", summary="Situation actuelle des données de ce calcul (avant optimisation)")
def get_run_situation(run_id: str) -> dict:
    d = _run_dir(run_id)
    if not (d / "situation.json").exists():
        raise HTTPException(404, "Ce calcul a été lancé avant l'enregistrement de la situation actuelle")
    return json.loads((d / "situation.json").read_text(encoding="utf-8"))


@app.get("/api/runs/{run_id}/trucks", summary="Camions (Sortie 2), filtrables")
def get_trucks(run_id: str, plant: str | None = None, supplier: str | None = None, day: str | None = None,
               limit: int = 100, offset: int = 0) -> dict:
    return results.trucks_table(_finished(run_id), plant, supplier, day, min(max(limit, 1), 500), max(offset, 0))


@app.get("/api/runs/{run_id}/trucks/{truck_id}", summary="Contenu d'un camion")
def get_truck(run_id: str, truck_id: str) -> dict:
    detail = results.truck_detail(_finished(run_id), truck_id)
    if detail is None:
        raise HTTPException(404, "Camion introuvable")
    return detail


@app.get("/api/runs/{run_id}/profiles", summary="Profils de livraison (Sortie 1)")
def get_profiles(run_id: str, plant: str | None = None, q: str | None = None) -> list[dict]:
    return results.profiles_table(_finished(run_id), plant, q)


@app.get("/api/runs/{run_id}/suppliers", summary="Économies par couple (Sortie 3)")
def get_suppliers(run_id: str) -> list[dict]:
    return results.suppliers_table(_finished(run_id))


@app.get("/api/runs/{run_id}/files/{name}", summary="Télécharger un fichier de sortie")
def get_file(run_id: str, name: str):
    out = _finished(run_id)
    if name == "gilbert_resultats.zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(DOWNLOADS):
                z.write(out / f, f)
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="gilbert_{run_id}.zip"'})
    if name not in DOWNLOADS:
        raise HTTPException(404, "Fichier inconnu")
    return FileResponse(out / name, filename=name)


@app.get("/api/defaults", summary="Fichiers et réglages par défaut")
def defaults() -> dict:
    return {"files": {k: v.exists() for k, v in DEFAULT_FILES.items()},
            "settings": Settings(import_id="").model_dump(exclude={"import_id", "missing_load"}),
            "cumulative_dates": list(situation.Config().cumulative_dates)}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "runs_dir": str(RUNS_DIR), "imports_dir": str(IMPORTS_DIR), "queue": sum(1 for s in state.values() if s.get("status") in ("en_attente", "en_cours"))}


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
