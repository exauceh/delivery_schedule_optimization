"""Execution of a stored run: read its inputs, run the pipeline, write its outputs and update its manifest.

Used by the CLI (in the same process) and by the API (in a separate process, see `api.jobs`).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .domain.parameters import dump_parameters, parse_parameters
from .io.readers import read_distances, read_rate_sheet, read_schedule
from .io.schemas import InputName
from .io.writers import write_tables, write_workbook
from .logging_config import run_log_file, setup_logging
from .pipeline import RawInputs, RunConfig, run_pipeline
from .reporting.brief import brief_tables
from .reporting.kpis import summary_table
from .storage.base import RunManifest, RunRepository, RunStatus
from .storage.filesystem import FileRunRepository

logger = logging.getLogger(__name__)


def app_version() -> str:
    try:
        return version("delivery-schedule-optimization")
    except PackageNotFoundError:
        return "unknown"


def execute_run(repository: RunRepository, run_id: str, workers: int | None = None) -> RunManifest:
    """Run the pipeline on the inputs of `run_id`; the manifest ends as succeeded or failed (never raises for the run)."""
    manifest = repository.get(run_id)
    manifest = manifest.model_copy(update={"status": RunStatus.RUNNING, "started_at": datetime.now(UTC), "error": None})
    repository.save(manifest)
    with run_log_file(repository.log_path(run_id)):
        logger.info("Run %s started", run_id)
        try:
            planner, preparation = parse_parameters(manifest.parameters)
            if workers is not None and "workers" not in (p if isinstance(p := manifest.parameters.get("planner"), dict) else {}):
                planner = replace(planner, workers=workers)
            inputs = RawInputs(
                schedule=read_schedule(repository.input_path(run_id, InputName.SCHEDULE)),
                distances=read_distances(repository.input_path(run_id, InputName.DISTANCES)),
                rate_sheet=read_rate_sheet(repository.input_path(run_id, InputName.RATE_SHEET)),
            )
            result = run_pipeline(inputs, RunConfig(planner=planner, preparation=preparation))
            write_tables(result.tables, repository.output_dir(run_id))
            # outputs 1, 2 and 3 in the layout of the project brief in the Excel report, as in the notebook in the CSV
            write_workbook({**result.tables, **brief_tables(result.tables)}, summary_table(result.kpis, result.rate_sheet, result.checks_passed,
                                                        sum(result.timings.values())), repository.output_dir(run_id))
        except Exception as exc:  # the run fails, the service keeps running: the error is recorded in the manifest
            logger.exception("Run %s failed", run_id)
            manifest = manifest.model_copy(update={"status": RunStatus.FAILED, "finished_at": datetime.now(UTC), "error": f"{type(exc).__name__}: {exc}"})
        else:
            manifest = manifest.model_copy(update={
                "status": RunStatus.SUCCEEDED, "finished_at": datetime.now(UTC), "timings": result.timings,
                "kpis": result.kpis, "accuracy": result.accuracy, "checks_passed": result.checks_passed,
                "parameters": dump_parameters(result.planner, preparation), "rate_sheet": result.rate_sheet,
                "plant_peaks": result.plant_peaks,
            })
            logger.info("Run %s succeeded in %.1f s", run_id, sum(result.timings.values()))
    repository.save(manifest)
    return manifest


def execute_run_in_subprocess(runs_dir: str, run_id: str, log_level: str, workers: int | None) -> None:
    """Entry point of the process that executes one run for the API (top-level so that it can be spawned)."""
    setup_logging(log_level)
    execute_run(FileRunRepository(Path(runs_dir)), run_id, workers)
