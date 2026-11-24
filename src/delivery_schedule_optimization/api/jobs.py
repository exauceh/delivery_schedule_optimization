"""Execution of the runs in the background, one at a time (the engine already uses every CPU core)."""

from __future__ import annotations

import logging
import multiprocessing
import queue
import threading
from datetime import UTC, datetime
from typing import Protocol

from ..service import execute_run, execute_run_in_subprocess
from ..settings import Settings
from ..storage.base import RunNotFoundError, RunRepository, RunStatus

logger = logging.getLogger(__name__)


class JobRunner(Protocol):
    def submit(self, run_id: str) -> None: ...

    def shutdown(self) -> None: ...


class ProcessJobRunner:
    """A queue served by one thread; each run executes in its own spawned process, so a crash cannot take the API down."""

    def __init__(self, repository: RunRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._serve, name="run-queue", daemon=True)
        self._thread.start()

    def submit(self, run_id: str) -> None:
        logger.info("Run %s queued (%d waiting)", run_id, self._queue.qsize())
        self._queue.put(run_id)

    def shutdown(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=5)

    def _serve(self) -> None:
        context = multiprocessing.get_context("spawn")
        while (run_id := self._queue.get()) is not None:
            process = context.Process(
                target=execute_run_in_subprocess,
                args=(str(self.settings.runs_dir), run_id, self.settings.log_level, self.settings.workers),
                name=f"run-{run_id}",
            )
            process.start()
            process.join()
            if process.exitcode != 0:
                self._mark_crashed(run_id, f"the run process exited with code {process.exitcode}")

    def _mark_crashed(self, run_id: str, reason: str) -> None:
        try:
            manifest = self.repository.get(run_id)
        except RunNotFoundError:
            return
        if manifest.status not in (RunStatus.SUCCEEDED, RunStatus.FAILED):
            logger.error("Run %s: %s", run_id, reason)
            self.repository.save(manifest.model_copy(update={"status": RunStatus.FAILED, "finished_at": datetime.now(UTC), "error": reason}))


class InlineJobRunner:
    """Executes a run immediately in the calling thread (tests)."""

    def __init__(self, repository: RunRepository, workers: int | None = None) -> None:
        self.repository = repository
        self.workers = workers

    def submit(self, run_id: str) -> None:
        execute_run(self.repository, run_id, self.workers)

    def shutdown(self) -> None:
        return None
