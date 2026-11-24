"""Reproduce only Wissam's retained zero-budget smoothing scenario."""
import argparse
from dataclasses import replace
import importlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter


COMMIT = "a0e8aba"


def benchmark(repo: Path, output: Path, workers: int) -> dict:
    start = perf_counter()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    inputs = output / "inputs"
    inputs.mkdir(exist_ok=True)

    def extract(path, name=None):
        content = subprocess.check_output([
            "git", "-c", f"safe.directory={repo.resolve().as_posix()}",
            "-C", str(repo.resolve()), "show", f"{COMMIT}:{path}",
        ])
        destination = inputs / (name or Path(path).name)
        destination.write_bytes(content)
        return destination

    extract("src/delivery_schedule_optimization/planner.py", "planner_wissam_a0e8aba.py")
    prepared = extract("data/consolidated_dataset_max.csv")
    raw_rates = extract("docs/rate_sheet.csv")
    raw_baseline = extract("data/baseline_deliveries_max.csv")
    sys.path.insert(0, str(inputs))
    planner = importlib.import_module("planner_wissam_a0e8aba")
    cfg = replace(planner.PlannerConfig(), workers=workers)
    lines = planner.load_lines(prepared, cfg)
    tariff = planner.Tariff(planner.load_rate_grid(raw_rates), cfg)
    holidays = planner.public_holidays(lines, cfg)
    reference, _ = planner.current_situation(lines, planner.load_baseline(raw_baseline, lines), tariff, cfg)
    input_time = perf_counter()
    candidates = planner.candidate_plans(lines, holidays, tariff, cfg)
    candidate_time = perf_counter()
    plans = planner.smooth(candidates, cfg)
    smooth_time = perf_counter()
    trucks, allocations = planner.export(lines, plans, "B", tariff, cfg)
    checks = planner.check(lines, plans, trucks, allocations, holidays, tariff, cfg, candidates)
    if not checks["respecté"].all():
        raise ValueError(checks.loc[~checks["respecté"]].to_string(index=False))
    trucks.to_csv(output / "camions.csv", index=False)
    allocations.to_csv(output / "affectations.csv", index=False)
    checks.to_csv(output / "controles.csv", index=False)
    end = perf_counter()
    return {
        "commit": COMMIT, "workers": workers, "candidates": sum(map(len, candidates.values())),
        "setup_and_baseline_s": round(input_time - start, 3),
        "candidates_s": round(candidate_time - input_time, 3),
        "smoothing_s": round(smooth_time - candidate_time, 3),
        "checks_and_exports_s": round(end - smooth_time, 3),
        "seconds_total": round(end - start, 3),
        "under_90_seconds": end - start < 90,
        "trucks": len(trucks), "cost_eur": round(float(trucks.cost.sum()), 2),
        "saving_eur": round(float(reference.cost.sum() - trucks.cost.sum()), 2),
        "peak": int(trucks.groupby("date").size().max()),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("results/milestone4-wissam"))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    result = benchmark(args.repo, args.output, args.workers)
    (args.output / "chrono.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
