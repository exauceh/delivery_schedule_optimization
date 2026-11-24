"""Command line: default parameters and a run on the small planning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from delivery_schedule_optimization.cli import EXIT_FAILED, EXIT_OK, main
from delivery_schedule_optimization.io.schemas import InputName


@pytest.fixture
def small_files(tmp_path: Path, small_inputs: dict[InputName, bytes]) -> dict[InputName, Path]:
    paths = {name: tmp_path / f"{name}.csv" for name in small_inputs}
    for name, content in small_inputs.items():
        paths[name].write_bytes(content)
    return paths


def test_parameters_prints_the_defaults(capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["parameters"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["planner"]["truck_kg"] == 24_500


def test_run_writes_the_outputs(small_files: dict[InputName, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    parameters = tmp_path / "parameters.json"
    parameters.write_text('{"planner": {"workers": 1}}', encoding="utf-8")
    code = main(["run", "--schedule", str(small_files[InputName.SCHEDULE]), "--distances", str(small_files[InputName.DISTANCES]),
                 "--rate-sheet", str(small_files[InputName.RATE_SHEET]), "--parameters", str(parameters), "--runs-dir", str(tmp_path / "runs")])
    assert code == EXIT_OK
    outputs = list((tmp_path / "runs").glob("*/output/sortie1_profils.csv"))
    assert len(outputs) == 1


def test_run_with_a_missing_file_fails_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    missing = str(tmp_path / "missing.csv")
    assert main(["run", "--schedule", missing, "--distances", missing, "--rate-sheet", missing]) == EXIT_FAILED
