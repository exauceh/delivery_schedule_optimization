"""Cancellation of a run: taken out of the queue while waiting, its process tree killed while running."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable, Iterator

import psutil
import pytest
from fastapi.testclient import TestClient

from delivery_schedule_optimization.api.app import create_app
from delivery_schedule_optimization.api.jobs import InlineJobRunner, ProcessJobRunner, kill_process_tree
from delivery_schedule_optimization.io.schemas import InputName
from delivery_schedule_optimization.settings import Settings
from delivery_schedule_optimization.storage.filesystem import FileRunRepository

FILES = {InputName.SCHEDULE: ("released_schedule.csv", "text/csv"), InputName.DISTANCES: ("distances.csv", "text/csv"),
         InputName.RATE_SHEET: ("rate_sheet.pdf", "application/pdf")}


def wait_for(condition: Callable[[], bool], timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.1)
    return False


def test_kill_process_tree() -> None:
    """The run process and the worker processes it started all die (they would otherwise keep the CPU busy)."""
    child = "import time; time.sleep(120)"
    parent = subprocess.Popen([sys.executable, "-c", f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(120)"])
    try:
        assert wait_for(lambda: bool(psutil.Process(parent.pid).children()), 30)
        tree = [psutil.Process(parent.pid), *psutil.Process(parent.pid).children(recursive=True)]
        kill_process_tree(parent.pid)
        assert not any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in tree)
    finally:
        parent.kill()
    kill_process_tree(parent.pid)  # already dead: nothing to do


@pytest.fixture
def process_client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """The application with its real runner: each run in its own spawned process."""
    settings = Settings(runs_dir=tmp_path_factory.mktemp("cancel") / "runs", workers=1)
    repository = FileRunRepository(settings.runs_dir)
    with TestClient(create_app(settings, repository, ProcessJobRunner(repository, settings))) as client:
        yield client


def launch(client: TestClient, inputs: dict[InputName, bytes], label: str) -> str:
    files = {name.value: (filename, inputs[name], mime) for name, (filename, mime) in FILES.items()}
    response = client.post("/api/runs", files=files, data={"label": label})
    assert response.status_code == 202, response.text
    return str(response.json()["run_id"])


def status(client: TestClient, run_id: str) -> str:
    return str(client.get(f"/api/runs/{run_id}").json()["status"])


def test_cancel_a_waiting_and_a_running_run(process_client: TestClient, small_inputs: dict[InputName, bytes]) -> None:
    client = process_client
    first, second = launch(client, small_inputs, "Premier"), launch(client, small_inputs, "Second")
    # the second run waits for the first one: cancelled, it never starts
    cancelled = client.post(f"/api/runs/{second}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled" and cancelled.json()["started_at"] is None
    # the first one is stopped while it runs
    assert wait_for(lambda: status(client, first) == "running", 60)
    run_processes = psutil.Process().children(recursive=True)  # the process of the first run (and its own children)
    assert run_processes
    assert client.post(f"/api/runs/{first}/cancel").json()["status"] == "cancelled"
    assert not any(p.is_running() and p.status() != psutil.STATUS_ZOMBIE for p in run_processes)
    # the queue goes on: a new run succeeds, and a finished run cannot be cancelled
    third = launch(client, small_inputs, "Troisième")
    assert wait_for(lambda: status(client, third) in ("succeeded", "failed"), 180)
    assert status(client, third) == "succeeded" and status(client, first) == status(client, second) == "cancelled"
    assert client.post(f"/api/runs/{third}/cancel").status_code == 409
    assert client.delete(f"/api/runs/{first}").status_code == 204  # a cancelled run can be deleted


def test_cancel_errors(tmp_path_factory: pytest.TempPathFactory, small_inputs: dict[InputName, bytes]) -> None:
    settings = Settings(runs_dir=tmp_path_factory.mktemp("cancel-inline") / "runs")
    repository = FileRunRepository(settings.runs_dir)
    with TestClient(create_app(settings, repository, InlineJobRunner(repository, workers=1))) as client:
        assert client.post("/api/runs/20260101-000000-abcdef/cancel").status_code == 404
        done = launch(client, small_inputs, "Fini")  # the inline runner has finished it when the request returns
        assert client.post(f"/api/runs/{done}/cancel").status_code == 409
