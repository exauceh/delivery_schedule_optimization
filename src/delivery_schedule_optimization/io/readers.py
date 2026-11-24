"""Reading of the three input files (path on disk or uploaded bytes) with a check of their columns."""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

import pandas as pd

from .schemas import NUMERIC_COLUMNS, REQUIRED_COLUMNS, InputError, InputName

logger = logging.getLogger(__name__)

Source = Path | bytes


def _read_csv(source: Source, name: InputName, **kwargs: object) -> pd.DataFrame:
    buffer = BytesIO(source) if isinstance(source, bytes) else source
    try:
        frame = pd.read_csv(buffer, **kwargs)  # type: ignore[call-overload]
    except (ValueError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise InputError(f"{name}: unreadable CSV file ({exc})") from exc
    if not isinstance(frame, pd.DataFrame):
        raise InputError(f"{name}: unreadable CSV file")
    if frame.empty:
        raise InputError(f"{name}: the file has no row")
    if missing := [c for c in REQUIRED_COLUMNS[name] if c not in frame.columns]:
        raise InputError(f"{name}: missing column(s) {missing}")
    for column in NUMERIC_COLUMNS[name]:
        converted = pd.to_numeric(frame[column], errors="coerce")
        if (bad := int((converted.isna() & frame[column].notna()).sum())) > 0:
            raise InputError(f"{name}: {bad} non-numeric value(s) in column '{column}'")
        frame[column] = converted
    logger.info("%s: %d rows x %d columns", name, *frame.shape)
    return frame


def read_schedule(source: Source) -> pd.DataFrame:
    """The released schedule: one row per need (date, supplier, material, plant, dock, quantity, weight, volume)."""
    frame = _read_csv(source, InputName.SCHEDULE, encoding="utf-8-sig")
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if (bad := int(dates.isna().sum())) > 0:
        raise InputError(f"{InputName.SCHEDULE}: {bad} unreadable date(s) in column 'date'")
    frame["date"] = dates
    return frame


def read_distances(source: Source) -> pd.DataFrame:
    """Distance of every supplier to the plants (km)."""
    return _read_csv(source, InputName.DISTANCES, encoding="utf-8-sig")


def read_rate_sheet(source: Source) -> pd.DataFrame:
    """Wide rate sheet: one row per distance bracket (`km_up_to`), one column per weight bracket."""
    return _read_csv(source, InputName.RATE_SHEET, dtype={"km_up_to": str})
