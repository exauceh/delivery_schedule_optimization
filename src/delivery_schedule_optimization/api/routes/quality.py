"""Data quality of an import, before launching the optimisation (nothing is stored)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from ...io.readers import read_distances, read_schedule
from ...io.schemas import InputError, InputName
from ...reporting.data_quality import DataQuality, data_quality
from ...settings import Settings
from ...storage.base import RunRepository
from ..dependencies import get_repository, get_settings_from_app
from .runs import _content

logger = logging.getLogger(__name__)

router = APIRouter(tags=["quality"])


def _report(schedule: bytes | Path, distances: bytes | Path) -> DataQuality:
    try:
        return data_quality(read_schedule(schedule), read_distances(distances))
    except (InputError, ValueError) as exc:  # unreadable file, or nothing left to plan
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/quality")
async def quality_of_files(
    released_schedule: Annotated[UploadFile, File(description="Released schedule (CSV)")],
    distances: Annotated[UploadFile, File(description="Distance of every supplier (CSV)")],
    settings: Annotated[Settings, Depends(get_settings_from_app)],
    rate_sheet: Annotated[UploadFile | None, File(description="Rate sheet (PDF), optional: only checked")] = None,
) -> DataQuality:
    """Quality of the dropped files: findings of the preparation and missing data, before launching the optimisation."""
    max_bytes = settings.max_upload_mb * 1_000_000
    schedule = await _content(released_schedule, InputName.SCHEDULE, max_bytes)
    distance_bytes = await _content(distances, InputName.DISTANCES, max_bytes)
    if rate_sheet is not None:
        await _content(rate_sheet, InputName.RATE_SHEET, max_bytes)
    return _report(schedule, distance_bytes)


@router.get("/quality/example")
def quality_of_example(settings: Annotated[Settings, Depends(get_settings_from_app)]) -> DataQuality:
    """Quality of the files of the project (the example of the interface)."""
    missing = [str(p) for p in (settings.example_schedule, settings.example_distances) if not p.is_file()]
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"example files not found on the server: {', '.join(missing)}")
    return _report(settings.example_schedule, settings.example_distances)


@router.get("/runs/{run_id}/quality")
def quality_of_run(run_id: str, repository: Annotated[RunRepository, Depends(get_repository)]) -> DataQuality:
    """Quality of the input files of a run (to relaunch it with other choices)."""
    repository.get(run_id)
    try:
        return _report(repository.input_path(run_id, InputName.SCHEDULE), repository.input_path(run_id, InputName.DISTANCES))
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"run {run_id} has no input files: {exc}") from exc

