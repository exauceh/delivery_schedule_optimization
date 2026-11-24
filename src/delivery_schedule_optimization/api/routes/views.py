"""Data of the web interface screens, built from the outputs of a finished run (see `reporting.dashboard`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ...io.readers import read_rate_sheet
from ...io.schemas import InputName
from ...pricing.tariff import Tariff, rate_grid
from ...reporting import dashboard
from ...storage.base import RunRepository, RunStatus
from ..dependencies import get_repository

router = APIRouter(prefix="/runs/{run_id}/views", tags=["interface"])


def _finished(repository: RunRepository, run_id: str) -> None:
    manifest = repository.get(run_id)
    if manifest.status is not RunStatus.SUCCEEDED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"run {run_id} is {manifest.status}: no result")


@router.get("/summary")
def summary(run_id: str, repository: Annotated[RunRepository, Depends(get_repository)]) -> dashboard.Summary:
    """Indicators, plants, calendar, profiles, smoothing, best savings, checks and output tests."""
    _finished(repository, run_id)
    return dashboard.summary(repository.output_dir(run_id), repository.get(run_id))


@router.get("/situation")
def situation(run_id: str, repository: Annotated[RunRepository, Depends(get_repository)]) -> dashboard.Situation:
    """Current situation of the run's data: indicators, anomaly register, rules applied, calendar, trucks on the rate sheet."""
    _finished(repository, run_id)
    sheet = read_rate_sheet(repository.input_path(run_id, InputName.RATE_SHEET))
    tariff = Tariff(rate_grid(sheet.table), sheet.price_factor)
    return dashboard.situation(repository.output_dir(run_id), repository.get(run_id), tariff)


@router.get("/trucks")
def trucks(
    run_id: str,
    repository: Annotated[RunRepository, Depends(get_repository)],
    plant: str | None = None,
    supplier: str | None = None,
    day: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 60,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dashboard.TruckPage:
    """Trucks of the plan (output 2), filtered by plant, supplier and day, one page at a time."""
    _finished(repository, run_id)
    return dashboard.trucks_page(repository.output_dir(run_id), plant, supplier, day, limit, offset)


@router.get("/trucks/{truck_id}")
def truck(run_id: str, truck_id: str, repository: Annotated[RunRepository, Depends(get_repository)]) -> dashboard.TruckDetail:
    """Content of one truck: materials, unloading points, need days, quantities."""
    _finished(repository, run_id)
    detail = dashboard.truck_detail(repository.output_dir(run_id), truck_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"truck not found: {truck_id}")
    return detail


@router.get("/profiles")
def profiles(run_id: str, repository: Annotated[RunRepository, Depends(get_repository)], plant: str | None = None,
             q: str | None = None) -> list[dashboard.ProfileRow]:
    """Delivery profile of every couple (output 1), filtered by plant and supplier."""
    _finished(repository, run_id)
    return dashboard.profile_rows(repository.output_dir(run_id), plant, q)


@router.get("/suppliers")
def suppliers(run_id: str, repository: Annotated[RunRepository, Depends(get_repository)]) -> list[dashboard.SupplierRow]:
    """Saving of every couple (output 3)."""
    _finished(repository, run_id)
    return dashboard.supplier_rows(repository.output_dir(run_id))
