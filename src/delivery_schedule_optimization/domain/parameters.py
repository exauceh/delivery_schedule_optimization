"""Parameters of a run as JSON: validation of user overrides and full record of the values used (reproducibility)."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

from pydantic import JsonValue, TypeAdapter, ValidationError

from .config import PlannerConfig, PreparationConfig

SECTIONS: dict[str, type[PlannerConfig] | type[PreparationConfig]] = {"planner": PlannerConfig, "preparation": PreparationConfig}


class ParameterError(ValueError):
    """Unknown parameter or value of the wrong type."""


def _section(cls: type[PlannerConfig] | type[PreparationConfig], values: JsonValue, name: str) -> PlannerConfig | PreparationConfig:
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ParameterError(f"'{name}' must be an object")
    known = {f.name for f in dataclasses.fields(cls)}
    if unknown := sorted(set(values) - known):
        raise ParameterError(f"unknown parameter(s) in '{name}': {unknown}; known: {sorted(known)}")
    try:
        result: PlannerConfig | PreparationConfig = TypeAdapter(cls).validate_python(dataclasses.asdict(cls()) | values)
    except ValidationError as exc:
        raise ParameterError(f"invalid value in '{name}': {exc.errors(include_url=False)}") from exc
    return result


def parse_parameters(parameters: Mapping[str, JsonValue] | None) -> tuple[PlannerConfig, PreparationConfig]:
    """Build the configurations from JSON overrides: {"planner": {...}, "preparation": {...}}; missing values keep their default."""
    parameters = parameters or {}
    if unknown := sorted(set(parameters) - set(SECTIONS)):
        raise ParameterError(f"unknown section(s) {unknown}; expected {sorted(SECTIONS)}")
    planner = _section(PlannerConfig, parameters.get("planner"), "planner")
    preparation = _section(PreparationConfig, parameters.get("preparation"), "preparation")
    assert isinstance(planner, PlannerConfig) and isinstance(preparation, PreparationConfig)
    return planner, preparation


def dump_parameters(planner: PlannerConfig, preparation: PreparationConfig) -> dict[str, JsonValue]:
    """Every value used by a run, defaults included, as JSON (stored in the run manifest)."""
    adapter: TypeAdapter[dict[str, JsonValue]] = TypeAdapter(dict[str, JsonValue])
    return adapter.validate_python({  # mode="json": tuples become lists, as in the JSON the API returns
        "planner": TypeAdapter(PlannerConfig).dump_python(planner, mode="json"),
        "preparation": TypeAdapter(PreparationConfig).dump_python(preparation, mode="json"),
    })
