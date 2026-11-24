"""Storage of the runs: what a run records (manifest) and the interface of a repository.

The files implementation (`filesystem.py`) is the only one for now; a database one (PostgreSQL) can be added behind
the same interface without changing the API.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, JsonValue

from ..io.schemas import InputName


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RunManifest(BaseModel):
    """Traceability of one run: inputs (SHA-256), parameters used, status, timings and main results."""

    run_id: str
    status: RunStatus = RunStatus.QUEUED
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    app_version: str
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    inputs: dict[str, str] = Field(default_factory=dict, description="input name -> SHA-256 of the uploaded file")
    timings: dict[str, float] = Field(default_factory=dict, description="stage -> seconds")
    kpis: dict[str, float] = Field(default_factory=dict)
    accuracy: dict[str, float] = Field(default_factory=dict)
    checks_passed: bool | None = None
    error: str | None = None


class RunNotFoundError(LookupError):
    """No run with this id."""


class RunRepository(Protocol):
    """Where the runs live: their input files, output files, manifest and log."""

    def create(self, inputs: Mapping[InputName, bytes], parameters: dict[str, JsonValue], app_version: str) -> RunManifest: ...

    def get(self, run_id: str) -> RunManifest: ...

    def list(self) -> list[RunManifest]: ...

    def save(self, manifest: RunManifest) -> None: ...

    def input_path(self, run_id: str, name: InputName) -> Path: ...

    def output_dir(self, run_id: str) -> Path: ...

    def log_path(self, run_id: str) -> Path: ...

    def archive(self, run_id: str) -> Path: ...
