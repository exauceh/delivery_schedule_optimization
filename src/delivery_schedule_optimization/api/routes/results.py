"""Results of a finished run: tables as JSON, CSV files, Excel report and zip archive."""

from __future__ import annotations

import json
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import JsonValue, TypeAdapter

from ...io.schemas import WORKBOOK_NAME, OutputName
from ...storage.base import RunRepository, RunStatus
from ..dependencies import get_repository
from ..schemas import TablePage

router = APIRouter(prefix="/runs/{run_id}", tags=["results"])

ROWS: TypeAdapter[list[list[JsonValue]]] = TypeAdapter(list[list[JsonValue]])


def _finished(repository: RunRepository, run_id: str) -> None:
    manifest = repository.get(run_id)
    if manifest.status is not RunStatus.SUCCEEDED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"run {run_id} is {manifest.status}: no result")


@router.get("/tables")
def list_tables(run_id: str, repository: Annotated[RunRepository, Depends(get_repository)]) -> list[OutputName]:
    """Names of the output tables of the run."""
    _finished(repository, run_id)
    return [name for name in OutputName if (repository.output_dir(run_id) / f"{name}.csv").is_file()]


@router.get("/tables/{name}")
def get_table(
    run_id: str,
    name: OutputName,
    repository: Annotated[RunRepository, Depends(get_repository)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=10_000)] = 1_000,
) -> TablePage:
    """One page of an output table (outputs 1 to 3, anomalies, checks...)."""
    _finished(repository, run_id)
    frame = pd.read_csv(repository.output_dir(run_id) / f"{name}.csv")
    page = json.loads(frame.iloc[offset:offset + limit].to_json(orient="split", index=False))
    return TablePage(name=name, total=len(frame), offset=offset, limit=limit, columns=[str(c) for c in frame.columns],
                     rows=ROWS.validate_python(page["data"]))


@router.get("/files/{name}")
def download_table(run_id: str, name: OutputName, repository: Annotated[RunRepository, Depends(get_repository)]) -> FileResponse:
    """One output table as a CSV file."""
    _finished(repository, run_id)
    return FileResponse(repository.output_dir(run_id) / f"{name}.csv", media_type="text/csv", filename=f"{name}.csv")


@router.get("/excel")
def download_workbook(run_id: str, repository: Annotated[RunRepository, Depends(get_repository)]) -> FileResponse:
    """Excel report of the run: a summary sheet, then the outputs, the checks and the output tests."""
    _finished(repository, run_id)
    path = repository.output_dir(run_id) / WORKBOOK_NAME
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"run {run_id} has no Excel report (made before the report existed)")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename=WORKBOOK_NAME)


@router.get("/download")
def download_all(run_id: str, repository: Annotated[RunRepository, Depends(get_repository)]) -> FileResponse:
    """Every output table of the run in a zip archive."""
    _finished(repository, run_id)
    return FileResponse(repository.archive(run_id), media_type="application/zip", filename=f"{run_id}.zip")
