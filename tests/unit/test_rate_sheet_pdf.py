"""Reading of the carrier's rate sheet in PDF: same grid as the CSV transcription, margin, surcharge and safety net."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from delivery_schedule_optimization.domain.config import PlannerConfig
from delivery_schedule_optimization.io.rate_sheet_pdf import _check_example
from delivery_schedule_optimization.io.readers import read_rate_sheet
from delivery_schedule_optimization.io.schemas import InputError, InputName

ROOT = Path(__file__).resolve().parents[2]
PDF = ROOT / "courses" / "rate_sheet.pdf"


def test_pdf_grid_is_the_csv_grid(input_paths: dict[InputName, Path]) -> None:
    """Every distance label, weight bracket and price of the PDF matches the CSV transcribed in Milestone 1."""
    pdf, csv = read_rate_sheet(PDF), read_rate_sheet(input_paths[InputName.RATE_SHEET])
    assert pdf.file_format == "pdf" and csv.file_format == "csv"
    assert list(pdf.table.columns) == list(csv.table.columns)
    assert pdf.table["km_up_to"].tolist() == csv.table["km_up_to"].tolist()
    assert np.array_equal(pdf.table.drop(columns="km_up_to").to_numpy(float), csv.table.drop(columns="km_up_to").to_numpy(float))


def test_pdf_gives_margin_surcharge_and_validity() -> None:
    sheet = read_rate_sheet(PDF)
    assert sheet.margin == PlannerConfig().margin == 2.3923
    assert sheet.diesel_surcharge == PlannerConfig().diesel_surcharge == 1.1943
    assert sheet.valid_from == "01.08.2026"


def test_csv_gives_no_terms(input_paths: dict[InputName, Path]) -> None:
    sheet = read_rate_sheet(input_paths[InputName.RATE_SHEET])
    assert sheet.margin is None and sheet.diesel_surcharge is None


def test_a_grid_shifted_by_one_row_is_refused() -> None:
    """The worked example of the PDF (300 km, 1 000 kg = 82.77 EUR) catches a label shift."""
    sheet = read_rate_sheet(PDF)
    shifted = sheet.table.copy()
    shifted["km_up_to"] = shifted["km_up_to"].shift(1).fillna("20")
    text = "1. Freight cost from table (300 km, 1,000 kg) 82.77 x (1 + 2.3923) EUR 82.77 EUR 335.34"
    _check_example(sheet.table, text, 2.3923, 1.1943)  # the real grid passes
    with pytest.raises(InputError, match="example of the PDF"):
        _check_example(shifted, text, 2.3923, 1.1943)


def test_a_wrong_margin_is_refused() -> None:
    sheet = read_rate_sheet(PDF)
    text = "(300 km, 1,000 kg) 82.77 x (1 + 2.3923) EUR 82.77 EUR 335.34"
    with pytest.raises(InputError, match="final price"):
        _check_example(sheet.table, text, 2.5, 1.1943)


def test_a_broken_pdf_is_refused() -> None:
    with pytest.raises(InputError, match="rate_sheet"):
        read_rate_sheet(b"%PDF-1.4 this is not really a PDF")
