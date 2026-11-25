"""Routes of the web interface: screens of a run, example, rerun with other parameters, deletion, Excel report, static files."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from delivery_schedule_optimization.api.app import create_app
from delivery_schedule_optimization.api.jobs import InlineJobRunner
from delivery_schedule_optimization.io.schemas import InputName
from delivery_schedule_optimization.settings import Settings
from delivery_schedule_optimization.storage.filesystem import FileRunRepository


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory, small_inputs: dict[InputName, bytes]) -> Iterator[TestClient]:
    """An application whose "example" is the small planning (fast), with one finished run."""
    folder = tmp_path_factory.mktemp("web")
    paths = {name: folder / f"{name}{'.pdf' if name is InputName.RATE_SHEET else '.csv'}" for name in InputName}
    for name, path in paths.items():
        path.write_bytes(small_inputs[name])
    settings = Settings(runs_dir=folder / "runs", example_schedule=paths[InputName.SCHEDULE],
                        example_distances=paths[InputName.DISTANCES], example_rate_sheet=paths[InputName.RATE_SHEET])
    repository = FileRunRepository(settings.runs_dir)
    with TestClient(create_app(settings, repository, InlineJobRunner(repository, workers=1))) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def run_id(client: TestClient) -> str:
    response = client.post("/api/runs/example", data={"label": "Plan de référence"})
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["status"] == "succeeded" and run["label"] == "Plan de référence" and run["plant_peaks"]
    return str(run["run_id"])


def test_the_interface_is_served(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "Gilbert" in page.text and 'src="app.js"' in page.text
    assert client.get("/app.js").status_code == 200 and client.get("/brand/gilbert-logo.svg").status_code == 200


def test_summary_adds_up(client: TestClient, run_id: str) -> None:
    summary = client.get(f"/api/runs/{run_id}/views/summary").json()
    kpi = summary["kpi"]
    assert kpi["saving"] == pytest.approx(kpi["cost_before"] - kpi["cost_after"])
    assert sum(p["trucks_after"] for p in summary["plants"]) == kpi["trucks_after"]
    assert sum(map(sum, summary["calendar"]["trucks"])) == kpi["trucks_after"]
    assert sum(a["lines"] for a in summary["advance"]) == kpi["lines"]
    assert sum(p["after"] for p in summary["profiles"]) == kpi["couples"]
    assert all(c["ok"] for c in summary["controls"]) and all(t["ok"] for t in summary["output_tests"])
    assert {s["plant"] for s in summary["smoothing"]} == set(summary["filters"]["plants"])


def test_situation_matches_the_current_cost(client: TestClient, run_id: str) -> None:
    situation = client.get(f"/api/runs/{run_id}/views/situation").json()
    summary = client.get(f"/api/runs/{run_id}/views/summary").json()
    assert situation["kpi"]["cost"] == pytest.approx(summary["kpi"]["cost_before"])
    assert sum(map(sum, situation["heatmap"]["trucks"])) == situation["kpi"]["trucks"] == summary["kpi"]["trucks_before"]
    assert len(situation["heatmap"]["price"]) == len(situation["heatmap"]["km"]) == 14
    assert {a["rule"] for a in situation["anomalies"]} >= {"DQ01", "DQ04", "DQ07"}
    assert all(a["label"] and not a["label"].startswith(("dropped", "flagged")) for a in situation["anomalies"])  # French labels
    assert len(situation["rules"]) >= 8


def test_trucks_profiles_and_suppliers(client: TestClient, run_id: str) -> None:
    base = f"/api/runs/{run_id}/views"
    page = client.get(f"{base}/trucks", params={"limit": 5}).json()
    assert len(page["rows"]) == 5 and page["total"] == client.get(f"/api/runs/{run_id}").json()["kpis"]["trucks_after"]
    first = page["rows"][0]
    plant_only = client.get(f"{base}/trucks", params={"plant": first["plant"], "day": first["date"]}).json()
    assert all(r["plant"] == first["plant"] and r["date"] == first["date"] for r in plant_only["rows"])
    truck = client.get(f"{base}/trucks/{first['id']}").json()
    assert truck["kg"] == pytest.approx(sum(i["kg"] for i in truck["items"])) and truck["replaces"] == first["replaces"]
    assert client.get(f"{base}/trucks/B-99999").status_code == 404
    profiles = client.get(f"{base}/profiles", params={"q": "sup_00085"}).json()
    assert {p["supplier"] for p in profiles} == {"SUP_00085"}
    suppliers = client.get(f"{base}/suppliers").json()
    assert sum(s["saving"] for s in suppliers) == pytest.approx(client.get(f"{base}/summary").json()["kpi"]["saving"])


def test_excel_report(client: TestClient, run_id: str) -> None:
    response = client.get(f"/api/runs/{run_id}/excel")
    assert response.status_code == 200
    sheets = pd.read_excel(io.BytesIO(response.content), sheet_name=None)
    assert {"Synthese", "Sortie1_Profils", "Sortie2_Camions", "Sortie3_Saving", "Controles", "Tests_sorties"} <= set(sheets)
    assert len(sheets["Sortie2_Camions"]) == client.get(f"/api/runs/{run_id}").json()["kpis"]["trucks_after"]
    # outputs 1, 2 and 3 start with the columns of the project brief (slides 10 to 12), in its order
    assert list(sheets["Sortie1_Profils"].columns[:8]) == ["Fournisseur", "Usine", "Distance (km)", "Livraisons / sem. actuelles",
                                                           "Fréquence cible", "Jour", "Camions / sem.", "Remplissage poids (%)"]
    assert sheets["Sortie1_Profils"]["Fréquence cible"].str.fullmatch(r"[1-5] / semaine").all()
    contents, trucks = sheets["Sortie2_Contenu_camions"], sheets["Sortie2_Camions"]
    assert {"Material", "Quai", "Besoins regroupés (jours)", "Quantité", "Poids (kg)"} <= set(contents.columns)
    assert (contents.groupby("Camion")["Material"].nunique().reindex(trucks["Camion"]).to_numpy() == trucks["Materials"].to_numpy()).all()
    assert (trucks["Livraisons remplacées"] >= 1).all()
    saving = sheets["Sortie3_Saving"]
    assert list(saving.columns[3:6]) == ["Coût actuel (EUR)", "Coût après consolidation (EUR)", "Saving (EUR)"]
    assert saving["Saving (EUR)"].sum() == pytest.approx(client.get(f"/api/runs/{run_id}").json()["kpis"]["saving"], abs=0.1)


def test_rerun_keeps_the_files_and_changes_the_parameters(client: TestClient, run_id: str) -> None:
    response = client.post(f"/api/runs/{run_id}/rerun", data={"label": "Sans lissage", "parameters": json.dumps({"planner": {"smoothing": False}})})
    assert response.status_code == 202, response.text
    rerun = response.json()
    source = client.get(f"/api/runs/{run_id}").json()
    assert rerun["source_run_id"] == run_id and rerun["inputs"] == source["inputs"] and rerun["label"] == "Sans lissage"
    assert rerun["parameters"]["planner"]["smoothing"] is False and rerun["status"] == "succeeded"
    assert client.post(f"/api/runs/{run_id}/rerun", data={"parameters": '{"planner": {"speed": 1}}'}).status_code == 422


def test_delete_a_run(client: TestClient, run_id: str) -> None:
    other = client.post(f"/api/runs/{run_id}/rerun", data={"label": "À supprimer"}).json()["run_id"]
    assert client.delete(f"/api/runs/{other}").status_code == 204
    assert client.get(f"/api/runs/{other}").status_code == 404
    assert other not in [r["run_id"] for r in client.get("/api/runs").json()]
    assert client.delete(f"/api/runs/{other}").status_code == 404


def test_views_of_an_unknown_run(client: TestClient) -> None:
    assert client.get("/api/runs/20260101-000000-abcdef/views/summary").status_code == 404


def test_example_files_missing(tmp_path: Path) -> None:
    settings = Settings(runs_dir=tmp_path / "runs", example_schedule=tmp_path / "missing.csv")
    repository = FileRunRepository(settings.runs_dir)
    with TestClient(create_app(settings, repository, InlineJobRunner(repository, workers=1))) as client:
        assert client.post("/api/runs/example").status_code == 404
