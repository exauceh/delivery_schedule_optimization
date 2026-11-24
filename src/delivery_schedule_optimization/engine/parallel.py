"""Run independent jobs in several processes (candidate plans of the couples, smoothing of the plants)."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor

_THREAD_VARIABLES = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")


def worker_count(requested: int | None, jobs: int) -> int:
    """Processes to use: `requested` (None = every CPU core), never more than the number of jobs."""
    return max(1, min(requested or os.cpu_count() or 1, jobs))


def parallel_map[Job, Result](function: Callable[[Job], Result], jobs: Sequence[Job], workers: int, chunksize: int = 1) -> list[Result]:
    """`[function(job) for job in jobs]`, spread over `workers` processes (in order; sequential when workers <= 1).

    `function` must be a top-level function so that it can run in another process.
    """
    if workers <= 1:
        return [function(job) for job in jobs]
    # One maths thread per process: the parallelism is between processes, and each OpenBLAS thread pool
    # would otherwise reserve its own memory at start-up.
    saved = {name: os.environ.get(name) for name in _THREAD_VARIABLES}
    os.environ.update({name: "1" for name in saved})
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(function, jobs, chunksize=chunksize))
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
