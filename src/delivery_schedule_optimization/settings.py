"""Technical settings of the application, read from the environment (prefix DSO_) or from a `.env` file.

Business parameters are not here: they belong to `domain.config` and can be overridden per run.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DSO_", env_file=".env", extra="ignore")

    runs_dir: Path = Path("runs")  # one folder per run: inputs, outputs, manifest, log
    log_level: str = "INFO"
    workers: int | None = None  # processes for the candidate plans (None = every CPU core)
    host: str = "127.0.0.1"
    port: int = 8000
    max_upload_mb: int = 50  # size limit of each uploaded file


@lru_cache
def get_settings() -> Settings:
    return Settings()
