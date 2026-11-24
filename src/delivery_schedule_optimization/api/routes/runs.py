"""Runs: import a planning and launch the optimisation, follow its progress."""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import PlainTextResponse

from ...domain.parameters import ParameterError, dump_parameters, parse_parameters
from ...io.readers import read_distances, read_rate_sheet, read_schedule
from ...io.schemas import InputError, InputName
from ...service import app_version
from ...settings import Settings
from ...storage.base import RunManifest, RunRepository
from ..dependencies import get_repository, get_runner, get_settings_from_app
from ..jobs import JobRunner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["runs"])

READERS = {InputName.SCHEDULE: read_schedule, InputName.DISTANCES: read_distances, InputName.RATE_SHEET: read_rate_sheet}


async def _content(upload: UploadFile, name: InputName, max_bytes: int) -> bytes:
    content = await upload.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"{name}: file larger than {max_bytes // 1_000_000} MB")
    try:
        READERS[name](content)  # check the columns now: the user gets the error at once, not after the queue
    except InputError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return content


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    released_schedule: Annotated[UploadFile, File(description="Released schedule (CSV): one row per need")],
    distances: Annotated[UploadFile, File(description="Distance of every supplier (CSV)")],
    rate_sheet: Annotated[UploadFile, File(description="Rate sheet of the carrier (PDF): price grid, margin and diesel surcharge")],
    repository: Annotated[RunRepository, Depends(get_repository)],
    runner: Annotated[JobRunner, Depends(get_runner)],
    settings: Annotated[Settings, Depends(get_settings_from_app)],
    parameters: Annotated[str | None, Form(description='Optional JSON overrides, e.g. {"planner": {"smoothing_budget": 0.005}}')] = None,
) -> RunManifest:
    """Import the three input files and launch the optimisation in the background; follow it with `GET /runs/{run_id}`."""
    max_bytes = settings.max_upload_mb * 1_000_000
    contents = {
        InputName.SCHEDULE: await _content(released_schedule, InputName.SCHEDULE, max_bytes),
        InputName.DISTANCES: await _content(distances, InputName.DISTANCES, max_bytes),
        InputName.RATE_SHEET: await _content(rate_sheet, InputName.RATE_SHEET, max_bytes),
    }
    try:
        overrides = json.loads(parameters) if parameters else {}
        if not isinstance(overrides, dict):
            raise ParameterError("parameters must be a JSON object")
        planner, preparation = parse_parameters(overrides)
    except (json.JSONDecodeError, ParameterError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"parameters: {exc}") from exc
    manifest = repository.create(contents, dump_parameters(planner, preparation), app_version())
    runner.submit(manifest.run_id)
    return repository.get(manifest.run_id)


@router.get("")
def list_runs(repository: Annotated[RunRepository, Depends(get_repository)]) -> list[RunManifest]:
    """Every run, most recent first."""
    return repository.list()


@router.get("/{run_id}")
def get_run(run_id: str, repository: Annotated[RunRepository, Depends(get_repository)]) -> RunManifest:
    """Status, parameters, input fingerprints, timings and main results of a run."""
    return repository.get(run_id)


@router.get("/{run_id}/log", response_class=PlainTextResponse)
def get_log(run_id: str, repository: Annotated[RunRepository, Depends(get_repository)]) -> str:
    """Log of the run."""
    repository.get(run_id)
    path = repository.log_path(run_id)
    return path.read_text(encoding="utf-8") if path.is_file() else ""
