"""Service status and default parameters."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import JsonValue

from ...domain.config import PlannerConfig, PreparationConfig
from ...domain.parameters import dump_parameters
from ...service import app_version
from ..schemas import Health

router = APIRouter(tags=["service"])


@router.get("/health")
def health() -> Health:
    """The service answers."""
    return Health(status="ok", version=app_version())


@router.get("/config/defaults")
def default_parameters() -> dict[str, JsonValue]:
    """Every parameter of a run with its default value; any of them can be overridden in `POST /runs`."""
    return dump_parameters(PlannerConfig(), PreparationConfig())
