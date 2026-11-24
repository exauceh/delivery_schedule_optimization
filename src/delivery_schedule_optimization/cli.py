"""Command line: `run` (one optimisation, no server), `serve` (the API) and `parameters` (default parameters as JSON)."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from .domain.config import PlannerConfig, PreparationConfig
from .domain.parameters import ParameterError, dump_parameters, parse_parameters
from .io.readers import read_distances, read_rate_sheet, read_schedule
from .io.schemas import InputError, InputName
from .logging_config import setup_logging
from .service import app_version, execute_run
from .settings import get_settings
from .storage.base import RunStatus
from .storage.filesystem import FileRunRepository

logger = logging.getLogger(__name__)

EXIT_OK, EXIT_FAILED, EXIT_RULES_VIOLATED = 0, 1, 2


def ensure_env_file() -> None:
    """Create `.env` from `.env.example` on first use, so that the settings are visible and editable."""
    env, example = Path(".env"), Path(".env.example")
    if not env.exists() and example.exists():
        shutil.copyfile(example, env)
        logger.warning("Created .env from .env.example: review the settings in .env if needed (no secret is required)")


def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    files = {InputName.SCHEDULE: args.schedule, InputName.DISTANCES: args.distances, InputName.RATE_SHEET: args.rate_sheet}
    readers = {InputName.SCHEDULE: read_schedule, InputName.DISTANCES: read_distances, InputName.RATE_SHEET: read_rate_sheet}
    try:
        contents = {name: Path(path).read_bytes() for name, path in files.items()}
        for name, content in contents.items():
            readers[name](content)
        overrides = json.loads(Path(args.parameters).read_text(encoding="utf-8")) if args.parameters else {}
        planner, preparation = parse_parameters(overrides)
    except (OSError, InputError, ParameterError, json.JSONDecodeError) as exc:
        logger.error("Cannot start the run: %s", exc)
        return EXIT_FAILED
    repository = FileRunRepository(Path(args.runs_dir) if args.runs_dir else settings.runs_dir)
    manifest = repository.create(contents, dump_parameters(planner, preparation), app_version())
    manifest = execute_run(repository, manifest.run_id, settings.workers)
    if manifest.status is not RunStatus.SUCCEEDED:
        logger.error("Run %s failed: %s (log: %s)", manifest.run_id, manifest.error, repository.log_path(manifest.run_id))
        return EXIT_FAILED
    k = manifest.kpis
    logger.info("Results in %s", repository.output_dir(manifest.run_id).resolve())
    logger.info("Cost %.0f -> %.0f EUR, saving %.0f EUR (%.1f %%), trucks %d -> %d, rules respected: %s", k["cost_before"], k["cost_after"],
                k["saving"], k["saving_pct"], k["trucks_before"], k["trucks_after"], manifest.checks_passed)
    return EXIT_OK if manifest.checks_passed else EXIT_RULES_VIOLATED


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api.app import create_app

    settings = get_settings()
    uvicorn.run(create_app(settings), host=args.host or settings.host, port=args.port or settings.port, log_level=settings.log_level.lower())
    return EXIT_OK


def _parameters(_: argparse.Namespace) -> int:
    sys.stdout.write(json.dumps(dump_parameters(PlannerConfig(), PreparationConfig()), indent=2) + "\n")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="delivery-schedule-optimization", description="Consolidation of the delivery schedule.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="optimise one planning and write the outputs in runs/<run_id>/output")
    run.add_argument("--schedule", required=True, type=Path, help="released schedule (CSV)")
    run.add_argument("--distances", required=True, type=Path, help="supplier distances (CSV)")
    run.add_argument("--rate-sheet", required=True, type=Path, help="rate sheet of the carrier: rate_sheet.pdf or a CSV")
    run.add_argument("--parameters", type=Path, help='JSON overrides, e.g. {"planner": {"smoothing_budget": 0.005}}')
    run.add_argument("--runs-dir", type=Path, help="folder of the runs (default: DSO_RUNS_DIR or ./runs)")
    run.set_defaults(handler=_run)
    serve = sub.add_parser("serve", help="start the API (documentation on /docs)")
    serve.add_argument("--host", help="default: DSO_HOST or 127.0.0.1")
    serve.add_argument("--port", type=int, help="default: DSO_PORT or 8000")
    serve.set_defaults(handler=_serve)
    params = sub.add_parser("parameters", help="print the default parameters as JSON (a starting point for --parameters)")
    params.set_defaults(handler=_parameters)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_env_file()
    get_settings.cache_clear()
    setup_logging(get_settings().log_level)
    code: int = args.handler(args)
    return code
