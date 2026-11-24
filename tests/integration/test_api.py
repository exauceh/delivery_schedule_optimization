"""API: import the three files, run (inline in the tests), read and download the results."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from delivery_schedule_optimization.api.app import create_app
from delivery_schedule_optimization.api.jobs import InlineJobRunner
from delivery_schedule_optimization.io.schemas import InputName
from delivery_schedule_optimization.settings import Settings
from delivery_schedule_optimization.storage.filesystem import FileRunRepository


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(runs_dir=tmp_path / "runs")
    repository = FileRunRepository(settings.runs_dir)
    with TestClient(create_app(settings, repository, InlineJobRunner(repository, workers=1))) as test_client:
        yield test_client


def _files(inputs: dict[InputName, bytes]) -> dict[str, tuple[str, bytes, str]]:
    return {str(name): (f"{name}.csv", content, "text/csv") for name, content in inputs.items()}


def test_health_and_default_parameters(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/config/defaults").json()["planner"]["max_advance_days"] == 7


def test_import_run_and_read_the_results(client: TestClient, small_inputs: dict[InputName, bytes]) -> None:
    response = client.post("/runs", files=_files(small_inputs), data={"parameters": json.dumps({"planner": {"smoothing_budget": 0.005}})})
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["status"] == "succeeded" and run["checks_passed"] is True
    assert run["parameters"]["planner"]["smoothing_budget"] == 0.005 and set(run["inputs"]) == {str(n) for n in InputName}

    assert client.get(f"/runs/{run['run_id']}").json()["kpis"]["saving"] >= 0
    assert [r["run_id"] for r in client.get("/runs").json()] == [run["run_id"]]
    assert "sortie1_profils" in client.get(f"/runs/{run['run_id']}/tables").json()

    page = client.get(f"/runs/{run['run_id']}/tables/sortie2_camions", params={"limit": 5}).json()
    assert page["total"] >= 1 and len(page["rows"]) <= 5 and "truck_id" in page["columns"]

    archive = client.get(f"/runs/{run['run_id']}/download")
    names = zipfile.ZipFile(io.BytesIO(archive.content)).namelist()
    assert {"sortie1_profils.csv", "sortie2_contenu_camions.csv", "sortie3_saving.csv"} <= set(names)
    assert "Run" in client.get(f"/runs/{run['run_id']}/log").text


def test_rate_sheet_in_pdf(client: TestClient, small_inputs: dict[InputName, bytes]) -> None:
    """The carrier's PDF is accepted as is: same results as the CSV, margin and surcharge taken from the PDF."""
    pdf = (Path(__file__).resolve().parents[2] / "courses" / "rate_sheet.pdf").read_bytes()
    with_csv = client.post("/runs", files=_files(small_inputs)).json()
    with_pdf = client.post("/runs", files=_files({**small_inputs, InputName.RATE_SHEET: pdf})).json()
    assert with_pdf["status"] == "succeeded", with_pdf["error"]
    assert with_pdf["rate_sheet"] == {"format": "pdf", "valid_from": "01.08.2026", "margin": 2.3923, "diesel_surcharge": 1.1943}
    assert with_csv["rate_sheet"]["format"] == "csv"
    assert with_pdf["kpis"] == with_csv["kpis"]


def test_file_with_a_missing_column_is_refused_at_once(client: TestClient, small_inputs: dict[InputName, bytes]) -> None:
    files = _files({**small_inputs, InputName.DISTANCES: b"supplier,km\nA,10\n"})
    response = client.post("/runs", files=files)
    assert response.status_code == 422 and "distances" in response.json()["detail"]
    assert client.get("/runs").json() == []


def test_unknown_parameter_is_refused(client: TestClient, small_inputs: dict[InputName, bytes]) -> None:
    response = client.post("/runs", files=_files(small_inputs), data={"parameters": '{"planner": {"speed": 3}}'})
    assert response.status_code == 422 and "speed" in response.json()["detail"]


def test_unknown_run_is_not_found(client: TestClient) -> None:
    assert client.get("/runs/20260101-000000-abcdef").status_code == 404
    assert client.get("/runs/../../etc").status_code == 404
