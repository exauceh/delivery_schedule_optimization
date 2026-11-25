"""Execution of the runs in the background, one at a time (the engine already uses every CPU core), and their cancellation."""

from __future__ import annotations

import contextlib
import logging
import multiprocessing
import queue
import threading
from datetime import UTC, datetime
from multiprocessing.process import BaseProcess
from typing import Protocol

import psutil

from ..service import execute_run, execute_run_in_subprocess
from ..settings import Settings
from ..storage.base import RunNotFoundError, RunRepository, RunStatus

logger = logging.getLogger(__name__)


FINISHED = (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED)


class JobRunner(Protocol):
    def submit(self, run_id: str) -> None: ...

    def cancel(self, run_id: str) -> bool:
        """Stop a waiting or running run; False if this runner is not waiting for it nor running it."""
        ...

    def shutdown(self) -> None: ...


def kill_process_tree(pid: int, timeout: float = 10.0) -> None:
    """Kill a process and every process it started (the worker processes of the candidate plans and of the smoothing)."""
    try:
        parent = psutil.Process(pid)
        processes = [*parent.children(recursive=True), parent]
    except psutil.NoSuchProcess:
        return
    for process in processes:
        with contextlib.suppress(psutil.NoSuchProcess):
            process.kill()
    _, alive = psutil.wait_procs(processes, timeout=timeout)
    if alive:
        logger.error("Processes still alive after kill: %s", [p.pid for p in alive])


class ProcessJobRunner:
    """A queue served by one thread; each run executes in its own spawned process, so a crash cannot take the API down."""

    def __init__(self, repository: RunRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._waiting: set[str] = set()  # queued runs not cancelled yet
        self._current: tuple[str, BaseProcess] | None = None  # run being executed and its process
        self._thread = threading.Thread(target=self._serve, name="run-queue", daemon=True)
        self._thread.start()

    def submit(self, run_id: str) -> None:
        with self._lock:
            self._waiting.add(run_id)
        logger.info("Run %s queued (%d waiting)", run_id, self._queue.qsize())
        self._queue.put(run_id)

    def cancel(self, run_id: str) -> bool:
        """A waiting run is taken out of the queue; a running one has its process tree killed. The run ends as cancelled."""
        with self._lock:
            if self._current is not None and self._current[0] == run_id:
                logger.info("Run %s: cancelled by the user, stopping its processes", run_id)
                process = self._current[1]
                if process.pid is not None:
                    kill_process_tree(process.pid)
            elif run_id in self._waiting:
                logger.info("Run %s: cancelled by the user before it started", run_id)
                self._waiting.discard(run_id)
            else:
                return False
            cancelled = self._finish(run_id, RunStatus.CANCELLED, None)
        if cancelled:  # the log of the run ends with the reason of its end
            with contextlib.suppress(OSError), self.repository.log_path(run_id).open("a", encoding="utf-8") as log:
                log.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} INFO    Run {run_id} cancelled by the user\n")
        return cancelled

    def shutdown(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=5)

    def _serve(self) -> None:
        context = multiprocessing.get_context("spawn")
        while (run_id := self._queue.get()) is not None:
            with self._lock:
                if run_id not in self._waiting:  # cancelled while it was waiting
                    continue
                self._waiting.discard(run_id)
                process = context.Process(
                    target=execute_run_in_subprocess,
                    args=(str(self.settings.runs_dir), run_id, self.settings.log_level, self.settings.workers),
                    name=f"run-{run_id}",
                )
                process.start()
                self._current = (run_id, process)
            process.join()
            with self._lock:
                self._current = None
                if process.exitcode != 0:  # a cancelled run is already marked cancelled: _finish leaves it as is
                    self._finish(run_id, RunStatus.FAILED, f"the run process exited with code {process.exitcode}")

    def _finish(self, run_id: str, status: RunStatus, error: str | None) -> bool:
        """Mark a run that has not finished yet as `status`; False if it had already finished (or was deleted)."""
        try:
            manifest = self.repository.get(run_id)
        except RunNotFoundError:
            return False
        if manifest.status in FINISHED:
            return False
        if error:
            logger.error("Run %s: %s", run_id, error)
        self.repository.save(manifest.model_copy(update={"status": status, "finished_at": datetime.now(UTC), "error": error}))
        return True


class InlineJobRunner:
    """Executes a run immediately in the calling thread (tests)."""

    def __init__(self, repository: RunRepository, workers: int | None = None) -> None:
        self.repository = repository
        self.workers = workers

    def submit(self, run_id: str) -> None:
        execute_run(self.repository, run_id, self.workers)

    def cancel(self, run_id: str) -> bool:
        return False  # the run is over when `submit` returns

    def shutdown(self) -> None:
        return None
