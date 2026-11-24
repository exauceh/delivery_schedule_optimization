"""Keep only date-opening improvements and attribute savings at pair level."""
import argparse
from pathlib import Path

import pandas as pd

from delivery_schedule_optimization.baseline import BaselineConfig


KEY = ["scope", "supplier_id", "plant"]
ANCHOR = "2026-11-09"


def run(before, after, output):
    a = pd.read_csv(before / "couples.csv")
    b = pd.read_csv(after / "couples.csv")
    merged = a.merge(b, on=KEY, suffixes=("_before", "_after"), validate="1:1")
    if (len(merged) != len(a) or len(merged) != len(b) or
            not merged.lines_before.eq(merged.lines_after).all() or
            not merged.reference_before.eq(merged.reference_after).all()):
        raise ValueError("Different comparison scopes or baselines")
    if merged.upper_after.gt(merged.upper_before).any():
        raise ValueError("The opened model must retain a verified incumbent")
    factor = BaselineConfig().price_factor / 100
    merged["improvement_eur"] = (merged.upper_before - merged.upper_after) * factor
    merged["use_earlier"] = merged.early_trucks.gt(0) & merged.upper_after.lt(merged.upper_before)
    merged["strictly_attributed"] = merged.use_earlier & merged.certified_before & merged.certified_after

    summaries = []
    for name, scope in merged.groupby("scope"):
        changed = scope.loc[scope.use_earlier]
        prior = scope.upper_before.sum() * factor
        attributable = changed.improvement_eur.sum()
        current = scope.upper_after.sum() * factor
        baseline = scope.reference_before.sum() * factor
        summaries.append(dict(scope=name, lines=int(scope.lines_before.sum()),
                              reference_eur=round(baseline, 2), cost_before_eur=round(prior, 2),
                              cost_controlled_eur=round(prior - attributable, 2),
                              cost_best_found_eur=round(current, 2),
                              improvement_open_days_eur=round(attributable, 2),
                              other_improvements_eur=round(prior-current-attributable, 2),
                              improved_pairs_using_early_days=len(changed),
                              certified_improved_pairs=int(changed.strictly_attributed.sum()),
                              early_trucks=int(changed.early_trucks.sum()),
                              early_lines=int(changed.early_lines.sum()),
                              controlled_trucks=int(scope.trucks_before.sum() +
                                                    (changed.trucks_after-changed.trucks_before).sum())))

    selection = merged[KEY + ["use_earlier"]]
    output.mkdir(parents=True, exist_ok=True)
    for name in ("affectations", "camions"):
        tables = []
        for source, use in ((before, False), (after, True)):
            table = pd.read_csv(source / f"{name}.csv")
            chosen = selection.loc[selection.use_earlier.eq(use), KEY]
            if name == "affectations" and "profile" not in table:
                table = table.merge(b[KEY + ["profile"]], on=KEY, validate="m:1")
            tables.append(table.merge(chosen, on=KEY, validate="m:1"))
        combined = pd.concat(tables, ignore_index=True)
        if name == "affectations":
            if combined.duplicated(["scope", "prepared_id"]).any():
                raise ValueError("Duplicate scheduled line")
            assert len(combined) == merged.lines_before.sum()
            assert (pd.to_datetime(combined.besoin) - pd.to_datetime(combined.date)).dt.days.between(0, 7).all()
            earlier = combined.loc[combined.date.lt(ANCHOR)]
            print(earlier.groupby(["scope", "date"]).size().to_string())
        combined.to_csv(output / f"{name}.csv", index=False)
    report = pd.DataFrame(summaries)
    merged.to_csv(output / "couples.csv", index=False)
    report.to_csv(output / "comparatif.csv", index=False)
    print(report.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, default=Path("results/meilleurs-couts-affines"))
    parser.add_argument("--after", type=Path, default=Path("results/anticipation-02-nov"))
    parser.add_argument("--output", type=Path, default=Path("results/impact-anticipation"))
    args = parser.parse_args()
    run(args.before, args.after, args.output)
