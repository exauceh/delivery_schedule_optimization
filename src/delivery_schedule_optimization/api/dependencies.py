"""Shared objects of the API, stored on the application at start-up."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from ..settings import Settings
from ..storage.base import RunRepository
from .jobs import JobRunner


def get_repository(request: Request) -> RunRepository:
    return cast(RunRepository, request.app.state.repository)


def get_runner(request: Request) -> JobRunner:
    return cast(JobRunner, request.app.state.runner)


def get_settings_from_app(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)
