"""Reading of the carrier's rate sheet PDF: same grid as the Milestone 1 transcription, terms and safety net."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from delivery_schedule_optimization.io.rate_sheet_pdf import _check_example
from delivery_schedule_optimization.io.readers import read_rate_sheet
from delivery_schedule_optimization.io.schemas import InputError, InputName

ROOT = Path(__file__).resolve().parents[2]
TRANSCRIPTION = ROOT / "docs" / "rate_sheet.csv"  # grid typed by hand from the PDF in Milestone 1
EXAMPLE = "1. Freight cost from table (300 km, 1,000 kg) 82.77 x (1 + 2.3923) EUR 82.77 EUR 280.78 EUR 335.34"


def test_pdf_grid_is_the_milestone_1_transcription(input_paths: dict[InputName, Path]) -> None:
    """Every distance label, weight bracket and price read from the PDF matches the grid typed in Milestone 1."""
    pdf = read_rate_sheet(input_paths[InputName.RATE_SHEET]).table
    typed = pd.read_csv(TRANSCRIPTION, dtype={"km_up_to": str})
    assert list(pdf.columns) == list(typed.columns)
    assert pdf["km_up_to"].tolist() == typed["km_up_to"].tolist()
    assert np.array_equal(pdf.drop(columns="km_up_to").to_numpy(float), typed.drop(columns="km_up_to").to_numpy(float))


def test_pdf_gives_margin_surcharge_and_validity(input_paths: dict[InputName, Path]) -> None:
    sheet = read_rate_sheet(input_paths[InputName.RATE_SHEET])
    assert (sheet.margin, sheet.diesel_surcharge, sheet.valid_from) == (2.3923, 1.1943, "01.08.2026")


def test_a_csv_rate_sheet_is_refused() -> None:
    with pytest.raises(InputError, match="PDF"):
        read_rate_sheet(TRANSCRIPTION)


def test_a_grid_shifted_by_one_row_is_refused(input_paths: dict[InputName, Path]) -> None:
    """The worked example of the PDF (300 km, 1 000 kg = 82.77 EUR) catches a shift of the distance labels."""
    grid = read_rate_sheet(input_paths[InputName.RATE_SHEET]).table
    _check_example(grid, EXAMPLE, 2.3923, 1.1943)  # the grid read passes
    shifted = grid.assign(km_up_to=grid["km_up_to"].shift(1).fillna("20"))
    with pytest.raises(InputError, match="example of the PDF"):
        _check_example(shifted, EXAMPLE, 2.3923, 1.1943)


def test_a_wrong_margin_is_refused(input_paths: dict[InputName, Path]) -> None:
    grid = read_rate_sheet(input_paths[InputName.RATE_SHEET]).table
    with pytest.raises(InputError, match="final price"):
        _check_example(grid, EXAMPLE, 2.5, 1.1943)


def test_a_broken_pdf_is_refused() -> None:
    with pytest.raises(InputError, match="rate_sheet"):
        read_rate_sheet(b"%PDF-1.4 this is not really a PDF")
