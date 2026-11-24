"""Data preparation (rules 1 to 9): the engine reads the raw files exactly as Milestone 1 did."""
from pathlib import Path

import pandas as pd
import pytest

from fusion.config import Config
from fusion.data import build_calendar, load_needs, load_rate_sheet

ROOT = Path(__file__).resolve().parent.parent


def raw_cfg(**kw) -> Config:
    return Config(consolidated_csv=None, schedule_csv=ROOT / "docs/released_schedule.csv",
                  distances_csv=ROOT / "docs/distances.csv", rate_sheet_csv=ROOT / "docs/rate_sheet.csv", **kw)


def test_rate_sheet_example_price():
    rates = load_rate_sheet(raw_cfg())
    assert rates.price(rates.row_of(300), 1000) == pytest.approx(335.34, abs=0.01)


def test_raw_files_give_the_milestone_1_lines():
    raw, log = load_needs(raw_cfg())
    m1, _ = load_needs(Config(consolidated_csv=ROOT / "data/consolidated_dataset.csv", rate_sheet_csv=ROOT / "docs/rate_sheet.csv"))
    cols = ["date", "supplier_id", "plant", "weight", "volume", "distance"]
    a = raw[cols].sort_values(cols).reset_index(drop=True).round(4)
    b = m1[cols].sort_values(cols).reset_index(drop=True).round(4)
    assert len(raw) == 4389 and a.equals(b)
    assert log["exclues_cumul"] == 420 and log["exclues_quantite_0"] == 918 and log["exclues_plus_grandes_qu_un_camion"] == 1


def test_no_missing_value_reaches_the_engine():
    for how in ("full_truck", "max", "median", "drop"):
        d, _ = load_needs(raw_cfg(missing_weight=how, missing_volume=how, missing_distance="drop" if how == "drop" else "max"))
        assert not d[["weight", "volume", "distance"]].isna().any().any()
        assert (d["weight"] > 0).all() and (d["volume"] > 0).all()
        assert (d["weight"] <= 24_500).all() and (d["volume"] <= 110.4).all()


def test_drop_removes_exactly_the_incomplete_lines():
    full, _ = load_needs(raw_cfg())
    dropped, log = load_needs(raw_cfg(missing_weight="drop", missing_volume="drop", missing_distance="drop"))
    assert log["exclues_weight_manquant"] == 134
    assert len(dropped) == len(full) - 134 - log["exclues_volume_manquant"] - log["exclues_distance_manquante"]


def test_median_is_lighter_than_full_truck():
    full, _ = load_needs(raw_cfg(missing_weight="full_truck"))
    med, _ = load_needs(raw_cfg(missing_weight="median"))
    assert med["weight"].sum() < full["weight"].sum()


def test_unknown_treatment_is_refused():
    with pytest.raises(ValueError):
        load_needs(raw_cfg(missing_weight="random"))


def test_calendar_starts_seven_days_before_the_first_need():
    cfg = raw_cfg()
    needs, _ = load_needs(cfg)
    cal = build_calendar(cfg, needs)
    assert str(cal.open_days[0]) == "2026-11-02"           # agreed: deliveries may start 7 days before 09/11
    late = build_calendar(raw_cfg(deliver_before_first_need=False), needs)
    assert str(late.open_days[0]) == "2026-11-09"
    assert all(d.weekday() < 5 for d in cal.open_days)
