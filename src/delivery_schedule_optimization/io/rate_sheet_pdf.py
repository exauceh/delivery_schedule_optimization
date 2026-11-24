"""Reading of the carrier's rate sheet in PDF (`rate_sheet.pdf`): price grid, margin, diesel surcharge, validity.

The grid is rebuilt from the POSITION of every word on the page, not from the extracted text: the text layout of
this PDF misplaces the distance labels (a row of prices seems to have no label and the last label no price), and a
reading line by line would shift every price by one distance bracket. Each row is the line of a distance label
(left of the first weight column) holding one price under each weight bracket of the header.

Safety net: the PDF prints a worked example (distance, weight, freight cost, final price). The grid read must give
the same freight cost for that distance and weight, and the margin and surcharge read must give the same final
price; otherwise the file is refused.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO

import numpy as np
import pandas as pd
import pdfplumber

from ..pricing.tariff import rate_grid
from .schemas import InputError, InputName, RateSheet

logger = logging.getLogger(__name__)

NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")
BRACKET = re.compile(r"^>?\d+(?:[.,]\d+)?$")
LINE_TOLERANCE = 2.0  # points: words whose tops differ by less belong to the same line
NAME = InputName.RATE_SHEET


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


def _float(value: object) -> float:
    if not isinstance(value, (int, float)):
        raise InputError(f"{NAME}: unexpected word position in the PDF")
    return float(value)


def _number(text: str) -> float:
    """A number written the English way, as in the whole PDF: "1,000" = one thousand, "82.77" = 82,77."""
    return float(text.replace(",", ""))


def _words(content: bytes) -> tuple[list[Word], str]:
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            if not pdf.pages:
                raise InputError(f"{NAME}: the PDF has no page")
            page = pdf.pages[0]
            words = [Word(str(w["text"]), _float(w["x0"]), _float(w["x1"]), _float(w["top"])) for w in page.extract_words()]
            text = page.extract_text() or ""
    except InputError:
        raise
    except Exception as exc:  # pdfminer raises many exception types on a broken file
        raise InputError(f"{NAME}: unreadable PDF ({type(exc).__name__}: {exc})") from exc
    return words, text


def _lines(words: Sequence[Word]) -> list[list[Word]]:
    """Group the words into lines (same top, within LINE_TOLERANCE), from top to bottom, each sorted left to right."""
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        if lines and abs(lines[-1][0].top - word.top) < LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def _grid(words: Sequence[Word]) -> pd.DataFrame:
    """Price grid in the wide format of the CSV rate sheet: `km_up_to` then one column per weight bracket."""
    lines = _lines(words)
    header_index = next((i for i, line in enumerate(lines) if any(w.text == "km" for w in line) and any(w.text == "kg" for w in line)), None)
    if header_index is None:
        raise InputError(f"{NAME}: header 'km \\ kg' of the price grid not found in the PDF")
    header = lines[header_index]
    kg_label_end = max(w.x1 for w in header if w.text == "kg")
    brackets = [w for w in header if w.x0 > kg_label_end and BRACKET.match(w.text)]
    if len(brackets) < 2:
        raise InputError(f"{NAME}: weight brackets not found in the header of the price grid")
    first_column = min(w.x0 for w in brackets)
    centers = np.array([w.center for w in brackets])
    half_gap = float(np.min(np.diff(centers))) / 2

    rows: list[list[str | float]] = []
    for line in lines[header_index + 1:]:
        labels = [w for w in line if w.x1 < first_column]
        prices = [w for w in line if w.x1 >= first_column]
        if len(labels) != 1 or not BRACKET.match(labels[0].text):
            if rows:
                break  # end of the grid
            continue
        row: list[str | float | None] = [labels[0].text, *([None] * len(brackets))]
        for word in prices:
            column = int(np.argmin(np.abs(centers - word.center)))
            if abs(centers[column] - word.center) > half_gap or row[column + 1] is not None or not NUMBER.match(word.text):
                raise InputError(f"{NAME}: unexpected value '{word.text}' in the row '{labels[0].text}' of the price grid")
            row[column + 1] = _number(word.text)
        if any(value is None for value in row):
            raise InputError(f"{NAME}: the row '{labels[0].text}' of the price grid has missing prices")
        rows.append([value for value in row if value is not None])
    if len(rows) < 2:
        raise InputError(f"{NAME}: price grid not found in the PDF")
    return pd.DataFrame(rows, columns=["km_up_to", *[w.text for w in brackets]])


def _search(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _check_example(grid: pd.DataFrame, text: str, margin: float, diesel: float) -> None:
    """Recompute the worked example printed in the PDF with the grid, margin and surcharge read."""
    lookup = re.search(r"\(([\d.,]+)\s*km,\s*([\d.,]+)\s*kg\)", text)
    base_text = _search(r"([\d.,]+)\s*x\s*\(1\s*\+", text)
    amounts = [_number(a) for a in re.findall(r"EUR\s*([\d,]+\.\d{2})", text)]
    if lookup is None or base_text is None or not amounts:
        logger.warning("%s: no worked example in the PDF, the grid read cannot be cross-checked", NAME)
        return
    km, kg, base = _number(lookup.group(1)), _number(lookup.group(2)), _number(base_text)
    numeric = rate_grid(grid)
    read = float(numeric.to_numpy()[np.searchsorted(numeric.index.to_numpy(), km), np.searchsorted(numeric.columns.to_numpy(), kg)])
    if abs(read - base) > 0.005:
        raise InputError(f"{NAME}: the grid read gives {read:.2f} EUR for {km:g} km and {kg:g} kg, the example of the PDF says {base:.2f} EUR")
    final = max(amounts)
    if abs(base * (1 + margin) * diesel - final) > 0.01:
        raise InputError(f"{NAME}: {base:.2f} x (1 + {margin}) x {diesel} does not give the final price of the example ({final:.2f} EUR)")
    logger.info("%s: worked example of the PDF recomputed (%g km, %g kg: %.2f EUR -> %.2f EUR)", NAME, km, kg, base, final)


def read_rate_sheet_pdf(content: bytes) -> RateSheet:
    """Read the rate sheet PDF: price grid (same format as the CSV), margin, diesel surcharge and validity date."""
    words, text = _words(content)
    grid = _grid(words)
    margin_text = _search(r"margin:\s*([\d.,]+)\s*%", text)
    diesel_text = _search(r"diesel surcharge:\s*([\d.,]+)", text)
    if margin_text is None or diesel_text is None:
        raise InputError(f"{NAME}: freight value margin or diesel surcharge not found in the PDF")
    margin, diesel = _number(margin_text) / 100, _number(diesel_text)
    _check_example(grid, text, margin, diesel)
    valid_from = _search(r"valid from:\s*([\d./-]+)", text)
    logger.info("%s: PDF grid %d distance x %d weight brackets, margin %.2f %%, diesel %.4f, valid from %s",
                NAME, len(grid), len(grid.columns) - 1, 100 * margin, diesel, valid_from)
    return RateSheet(table=grid, margin=margin, diesel_surcharge=diesel, valid_from=valid_from)
