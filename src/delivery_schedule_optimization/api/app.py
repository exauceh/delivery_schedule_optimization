"""FastAPI application: the API under /api (import, run in the background, results) and the web interface on /."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..service import app_version
from ..settings import Settings, get_settings
from ..storage.base import RunNotFoundError, RunRepository, RunStatus
from ..storage.filesystem import FileRunRepository
from .jobs import JobRunner, ProcessJobRunner
from .routes import health, quality, results, runs, views

WEB_DIR = Path(__file__).resolve().parent.parent / "web"  # the interface: HTML, CSS, JS, logo, fonts

logger = logging.getLogger(__name__)


def _close_interrupted_runs(repository: RunRepository) -> None:
    """A run left queued or running by a previous stop of the service will never finish: mark it failed."""
    for manifest in repository.list():
        if manifest.status in (RunStatus.QUEUED, RunStatus.RUNNING):
            logger.warning("Run %s was interrupted by a restart of the service", manifest.run_id)
            repository.save(manifest.model_copy(update={"status": RunStatus.FAILED, "finished_at": datetime.now(UTC),
                                                        "error": "interrupted by a restart of the service"}))


def index_page() -> HTMLResponse:
    """The interface page, its stylesheet and script addressed by a fingerprint of their content (`styles.css?v=...`):
    a new version is a new address, so a browser never mixes a new page with an old cached stylesheet."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    for asset in ("styles.css", "app.js"):
        digest = hashlib.sha256((WEB_DIR / asset).read_bytes()).hexdigest()[:12]
        html = html.replace(f'"{asset}"', f'"{asset}?v={digest}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


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

    @app.middleware("http")
    async def revalidate_interface(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """The browser asks again for the interface files (304 if unchanged), so a new version never meets an old stylesheet."""
        response = await call_next(request)
        if not request.url.path.startswith("/api"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    for router in (health.router, quality.router, runs.router, results.router, views.router):
        app.include_router(router, prefix="/api")
    if WEB_DIR.is_dir():  # mounted last: every path that is not an API route serves the interface
        app.add_api_route("/", index_page, methods=["GET"], include_in_schema=False)
        app.add_api_route("/index.html", index_page, methods=["GET"], include_in_schema=False)
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    else:
        logger.warning("Web interface not found in %s: API only", WEB_DIR)
    return app
