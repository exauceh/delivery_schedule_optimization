"""Milestone 4, suites A1 and A3: unit cases of the engine functions, on the real planning (`validation.engine_cases`)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from delivery_schedule_optimization.validation import engine_cases
from delivery_schedule_optimization.validation.engine_cases import EngineInputs
from delivery_schedule_optimization.validation.output_tests import TestOutcome

SLOW = {engine_cases.test_reassign_never_worse}  # about 20 s: 150 plans optimised then reassigned

CASES = [pytest.param(fn, names, id=fn.__name__, marks=[pytest.mark.slow] if fn in SLOW else [])
         for _, fn, names in engine_cases.CASES]


@pytest.mark.parametrize(("case", "names"), CASES)
def test_engine_case(engine_inputs: EngineInputs, case: Callable[..., TestOutcome], names: tuple[str, ...]) -> None:
    passed, detail = case(*engine_cases.case_arguments(names, engine_inputs))
    assert passed, detail
