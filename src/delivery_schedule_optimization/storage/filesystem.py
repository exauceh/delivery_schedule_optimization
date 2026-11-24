"""Runs stored as folders: runs/<run_id>/{input/, output/, manifest.json, run.log}."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from pydantic import JsonValue

from ..io.schemas import InputName, file_suffix
from ..io.writers import zip_directory
from .base import RunManifest, RunNotFoundError

logger = logging.getLogger(__name__)

RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")


class FileRunRepository:
    """`RunRepository` on the local disk (a Docker volume in production)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, run_id: str) -> Path:
        if not RUN_ID.match(run_id):  # also prevents path traversal through the id
            raise RunNotFoundError(run_id)
        return self.root / run_id

    def create(self, inputs: Mapping[InputName, bytes], parameters: dict[str, JsonValue], app_version: str) -> RunManifest:
        run_id = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        folder = self._dir(run_id)
        (folder / "input").mkdir(parents=True)
        hashes: dict[str, str] = {}
        for name, content in inputs.items():
            (folder / "input" / f"{name}{file_suffix(content)}").write_bytes(content)
            hashes[str(name)] = hashlib.sha256(content).hexdigest()
        manifest = RunManifest(run_id=run_id, created_at=datetime.now(UTC), app_version=app_version, parameters=parameters, inputs=hashes)
        self.save(manifest)
        logger.info("Run %s created (inputs: %s)", run_id, {k: v[:12] for k, v in hashes.items()})
        return manifest

    def get(self, run_id: str) -> RunManifest:
        path = self._dir(run_id) / "manifest.json"
        if not path.is_file():
            raise RunNotFoundError(run_id)
        return RunManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[RunManifest]:
        runs = [self.get(p.name) for p in self.root.iterdir() if RUN_ID.match(p.name) and (p / "manifest.json").is_file()]
        return sorted(runs, key=lambda m: m.created_at, reverse=True)

    def save(self, manifest: RunManifest) -> None:
        path = self._dir(manifest.run_id) / "manifest.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic: a reader never sees a half-written manifest

    def input_path(self, run_id: str, name: InputName) -> Path:
        """The stored input file (.csv, or .pdf for the rate sheet)."""
        matches = sorted((self._dir(run_id) / "input").glob(f"{name}.*"))
        if not matches:
            raise FileNotFoundError(f"run {run_id}: input {name} not found")
        return matches[0]

    def output_dir(self, run_id: str) -> Path:
        return self._dir(run_id) / "output"

    def log_path(self, run_id: str) -> Path:
        return self._dir(run_id) / "run.log"

    def archive(self, run_id: str) -> Path:
        """Zip of the outputs of a run (created once)."""
        path = self._dir(run_id) / f"{run_id}.zip"
        if not path.is_file():
            zip_directory(self.output_dir(run_id), path)
        return path
