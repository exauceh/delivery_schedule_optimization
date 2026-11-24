"""Response models of the API (the run manifest itself is `storage.base.RunManifest`)."""

from __future__ import annotations

from pydantic import BaseModel, JsonValue


class Health(BaseModel):
    status: str
    version: str


class TablePage(BaseModel):
    """A page of an output table: column names and rows, in the order of the CSV file."""

    name: str
    total: int
    offset: int
    limit: int
    columns: list[str]
    rows: list[list[JsonValue]]
