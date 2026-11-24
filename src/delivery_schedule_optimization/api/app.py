"""FastAPI application: import a planning, run the optimisation in the background, read and download the results."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from ..service import app_version
from ..settings import Settings, get_settings
from ..storage.base import RunNotFoundError, RunRepository, RunStatus
from ..storage.filesystem import FileRunRepository
from .jobs import JobRunner, ProcessJobRunner
from .routes import health, results, runs

logger = logging.getLogger(__name__)


def _close_interrupted_runs(repository: RunRepository) -> None:
    """A run left queued or running by a previous stop of the service will never finish: mark it failed."""
    for manifest in repository.list():
        if manifest.status in (RunStatus.QUEUED, RunStatus.RUNNING):
            logger.warning("Run %s was interrupted by a restart of the service", manifest.run_id)
            repository.save(manifest.model_copy(update={"status": RunStatus.FAILED, "finished_at": datetime.now(UTC),
                                                        "error": "interrupted by a restart of the service"}))


def create_app(settings: Settings | None = None, repository: RunRepository | None = None, runner: JobRunner | None = None) -> FastAPI:
    """Build the application; tests pass their own repository and runner."""
    settings = settings or get_settings()
    repository = repository or FileRunRepository(settings.runs_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _close_interrupted_runs(repository)
        app.state.settings = settings
        app.state.repository = repository
        app.state.runner = runner or ProcessJobRunner(repository, settings)
        logger.info("Service started: runs in %s", settings.runs_dir.resolve())
        yield
        app.state.runner.shutdown()

    app = FastAPI(
        title="Consolidation du planning de livraisons",
        description="Import a released schedule, the supplier distances and the rate sheet; get the delivery profiles, "
                    "the trucks and their contents, and the savings.",
        version=app_version(),
        lifespan=lifespan,
    )

    @app.exception_handler(RunNotFoundError)
    async def run_not_found(_: Request, exc: RunNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": f"run not found: {exc}"})

    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(results.router)
    return app
