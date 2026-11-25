"""Runs: import a planning and launch the optimisation, relaunch it with other parameters, follow it, cancel it, delete it."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import PlainTextResponse
from pydantic import JsonValue

from ...domain.parameters import ParameterError, dump_parameters, parse_parameters
from ...io.readers import read_distances, read_rate_sheet, read_schedule
from ...io.schemas import InputError, InputName
from ...service import app_version
from ...settings import Settings
from ...storage.base import RunManifest, RunRepository, RunStatus
from ..dependencies import get_repository, get_runner, get_settings_from_app
from ..jobs import JobRunner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])

READERS = {InputName.SCHEDULE: read_schedule, InputName.DISTANCES: read_distances, InputName.RATE_SHEET: read_rate_sheet}
LabelForm = Annotated[str, Form(min_length=1, max_length=60, description="Name of the scenario, shown by the interface")]
ParametersForm = Annotated[str | None, Form(description='Optional JSON overrides, e.g. {"planner": {"smoothing_budget": 0.005}}')]


def _checked(content: bytes, name: InputName) -> bytes:
    try:
        READERS[name](content)  # check the columns now: the user gets the error at once, not after the queue
    except InputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return content


async def _content(upload: UploadFile, name: InputName, max_bytes: int) -> bytes:
    content = await upload.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"{name}: file larger than {max_bytes // 1_000_000} MB")
    return _checked(content, name)


def _parameters(parameters: str | None) -> dict[str, JsonValue]:
    try:
        overrides = json.loads(parameters) if parameters else {}
        if not isinstance(overrides, dict):
            raise ParameterError("parameters must be a JSON object")
        planner, preparation = parse_parameters(overrides)
    except (json.JSONDecodeError, ParameterError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"parameters: {exc}") from exc
    return dump_parameters(planner, preparation)


def _launch(repository: RunRepository, runner: JobRunner, contents: Mapping[InputName, bytes], parameters: str | None, label: str,
            file_names: Mapping[InputName, str], source_run_id: str | None = None) -> RunManifest:
    manifest = repository.create(contents, _parameters(parameters), app_version(), label=label.strip(), source_run_id=source_run_id)
    # the names of the files, for the history of the interface (the content is identified by its SHA-256)
    repository.save(manifest.model_copy(update={"input_files": {name.value: file_name for name, file_name in file_names.items()}}))
    runner.submit(manifest.run_id)
    return repository.get(manifest.run_id)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    released_schedule: Annotated[UploadFile, File(description="Released schedule (CSV): one row per need")],
    distances: Annotated[UploadFile, File(description="Distance of every supplier (CSV)")],
    rate_sheet: Annotated[UploadFile, File(description="Rate sheet of the carrier (PDF): price grid, margin and diesel surcharge")],
    repository: Annotated[RunRepository, Depends(get_repository)],
    runner: Annotated[JobRunner, Depends(get_runner)],
    settings: Annotated[Settings, Depends(get_settings_from_app)],
    parameters: ParametersForm = None,
    label: LabelForm = "Scénario",
) -> RunManifest:
    """Import the three input files and launch the optimisation in the background; follow it with `GET /runs/{run_id}`."""
    max_bytes = settings.max_upload_mb * 1_000_000
    contents = {
        InputName.SCHEDULE: await _content(released_schedule, InputName.SCHEDULE, max_bytes),
        InputName.DISTANCES: await _content(distances, InputName.DISTANCES, max_bytes),
        InputName.RATE_SHEET: await _content(rate_sheet, InputName.RATE_SHEET, max_bytes),
    }
    uploads = {InputName.SCHEDULE: released_schedule, InputName.DISTANCES: distances, InputName.RATE_SHEET: rate_sheet}
    return _launch(repository, runner, contents, parameters, label, {name: upload.filename or name.value for name, upload in uploads.items()})


@router.post("/example", status_code=status.HTTP_202_ACCEPTED)
def create_example_run(
    repository: Annotated[RunRepository, Depends(get_repository)],
    runner: Annotated[JobRunner, Depends(get_runner)],
    settings: Annotated[Settings, Depends(get_settings_from_app)],
    parameters: ParametersForm = None,
    label: LabelForm = "Exemple",
) -> RunManifest:
    """Launch the optimisation on the files of the project (November 2026 planning, distances, rate sheet PDF)."""
    paths = {InputName.SCHEDULE: settings.example_schedule, InputName.DISTANCES: settings.example_distances,
             InputName.RATE_SHEET: settings.example_rate_sheet}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        logger.error("Example files not found: %s", missing)
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"example files not found on the server: {', '.join(missing)}")
    return _launch(repository, runner, {name: path.read_bytes() for name, path in paths.items()}, parameters, label,
                   {name: path.name for name, path in paths.items()})


@router.post("/{run_id}/rerun", status_code=status.HTTP_202_ACCEPTED)
def rerun(
    run_id: str,
    repository: Annotated[RunRepository, Depends(get_repository)],
    runner: Annotated[JobRunner, Depends(get_runner)],
    parameters: ParametersForm = None,
    label: LabelForm = "Scénario",
) -> RunManifest:
    """Launch a new run on the input files of `run_id`, with other parameters: a new scenario of the same planning."""
    source = repository.get(run_id)
    try:
        contents = {name: repository.input_path(run_id, name).read_bytes() for name in InputName}
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"run {run_id} has no input files to reuse: {exc}") from exc
    names = {name: source.input_files.get(name.value, name.value) for name in InputName}
    return _launch(repository, runner, contents, parameters, label, names, source_run_id=run_id)


@router.get("")
def list_runs(repository: Annotated[RunRepository, Depends(get_repository)]) -> list[RunManifest]:
    """Every run, most recent first."""
    return repository.list()


@router.get("/{run_id}")
def get_run(run_id: str, repository: Annotated[RunRepository, Depends(get_repository)]) -> RunManifest:
    """Status, parameters, input fingerprints, timings and main results of a run."""
    return repository.get(run_id)


@router.post("/{run_id}/cancel")
def cancel_run(
    run_id: str,
    repository: Annotated[RunRepository, Depends(get_repository)],
    runner: Annotated[JobRunner, Depends(get_runner)],
) -> RunManifest:
    """Stop a waiting or running run: it ends as cancelled and keeps no results."""
    manifest = repository.get(run_id)
    if manifest.status not in (RunStatus.QUEUED, RunStatus.RUNNING) or not runner.cancel(run_id):
        raise HTTPException(status.HTTP_409_CONFLICT, f"run {run_id} is {repository.get(run_id).status}: nothing to cancel")
    return repository.get(run_id)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(run_id: str, repository: Annotated[RunRepository, Depends(get_repository)]) -> Response:
    """Delete a finished run (succeeded, failed or cancelled) and its files."""
    manifest = repository.get(run_id)
    if manifest.status in (RunStatus.QUEUED, RunStatus.RUNNING):
        raise HTTPException(status.HTTP_409_CONFLICT, f"run {run_id} is {manifest.status}: wait for its end before deleting it")
    repository.delete(run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{run_id}/log", response_class=PlainTextResponse)
def get_log(run_id: str, repository: Annotated[RunRepository, Depends(get_repository)]) -> str:
    """Log of the run."""
    repository.get(run_id)
    path = repository.log_path(run_id)
    return path.read_text(encoding="utf-8") if path.is_file() else ""
