"""Non-regression: on the real planning, the application gives exactly the outputs of the milestone 3 notebook.

The expected files (`expected/`) are the outputs 1, 2 and 3 written by `milestone3/milestone3.ipynb`; output 1 keeps only
the columns of the project brief, renamed as in the brief (`io.schemas.PROFILE_COLUMNS`), with the notebook values. Any change of
the engine that changes a result, even one cent or one line moved to another truck, makes this test fail. The run starts
from the three raw input files, the carrier's rate sheet being read from its PDF.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from delivery_schedule_optimization.domain.config import PlannerConfig
from delivery_schedule_optimization.io.readers import read_distances, read_rate_sheet, read_schedule
from delivery_schedule_optimization.io.schemas import InputName, OutputName
from delivery_schedule_optimization.io.writers import write_tables
from delivery_schedule_optimization.pipeline import RawInputs, RunConfig, run_pipeline

EXPECTED = Path(__file__).parent / "expected"
COMPARED = [OutputName.PROFILES, OutputName.TRUCK_CONTENTS, OutputName.TRUCKS, OutputName.ALLOCATIONS, OutputName.SAVING]


@pytest.mark.slow
def test_outputs_are_those_of_the_milestone_3_notebook(input_paths: dict[InputName, Path], tmp_path: Path) -> None:
    inputs = RawInputs(
        schedule=read_schedule(input_paths[InputName.SCHEDULE]),
        distances=read_distances(input_paths[InputName.DISTANCES]),
        rate_sheet=read_rate_sheet(input_paths[InputName.RATE_SHEET]),
    )
    result = run_pipeline(inputs, RunConfig(planner=PlannerConfig()))
    assert result.checks_passed
    tests = result.tables[OutputName.OUTPUT_TESTS]  # milestone 4 suites on the real outputs, H06 (SUP_00130) included
    assert tests["respecté"].all(), tests.loc[~tests["respecté"], ["fonction", "détail"]].to_dict("records")
    written = write_tables({name: result.tables[name] for name in COMPARED}, tmp_path)
    for name in COMPARED:
        actual = written[name].read_text(encoding="utf-8").replace("\r\n", "\n")
        expected = (EXPECTED / f"{name}.csv").read_text(encoding="utf-8").replace("\r\n", "\n")
        assert actual == expected, f"{name} differs from the milestone 3 notebook"
    assert result.kpis["saving"] == pytest.approx(66_267, abs=1)
