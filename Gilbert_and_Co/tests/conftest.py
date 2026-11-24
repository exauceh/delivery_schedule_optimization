"""Shared fixtures. The API stores imports and runs in folders read at import time: point them to a temporary folder
before anything imports api.main."""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="gilbert_tests_"))
os.environ["GILBERT_RUNS_DIR"] = str(TMP / "runs")
os.environ["GILBERT_IMPORTS_DIR"] = str(TMP / "imports")

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


@pytest.fixture(scope="session")
def docs() -> Path:
    return DOCS


@pytest.fixture()
def import_dir(tmp_path: Path) -> Path:
    """A folder laid out like an import: the three project files."""
    for name in ("released_schedule.csv", "distances.csv", "rate_sheet.csv"):
        shutil.copy(DOCS / name, tmp_path / name)
    return tmp_path


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)
