"""Current situation and anomaly register shown before the optimisation."""
import pandas as pd

from api.situation import analyse, check_columns, describe


def test_current_situation_matches_milestone_2(import_dir):
    s = analyse(str(import_dir))
    k = s["kpi"]
    assert (k["lines"], k["deliveries"], k["trucks"]) == (4389, 1665, 1741)
    assert k["cost"] == pytest_approx(176_724.61)
    assert (k["first"], k["last"]) == ("2026-11-09", "2026-12-18")
    assert sum(map(sum, s["heatmap"]["trucks"])) == k["trucks"]
    assert sum(map(sum, s["calendar"]["trucks"])) == k["trucks"]
    assert sum(p["cost"] for p in s["plants"]) == pytest_approx(k["cost"])


def test_anomaly_register(import_dir):
    reg = {a["code"]: a for a in analyse(str(import_dir))["anomalies"]}
    assert reg["cumul"]["count"] == 420 and reg["zero"]["count"] == 918 and reg["too_big"]["count"] == 1
    assert reg["weight"]["count"] == 134 and reg["volume"]["count"] == 116 and reg["distance"]["count"] == 4
    assert reg["closed"]["count"] == 0


def test_rules_follow_the_chosen_treatment(import_dir):
    rules = " ".join(analyse(str(import_dir), weight="drop")["rules"])
    assert "Poids manquant : 134 lignes retirées" in rules
    rules = " ".join(analyse(str(import_dir), weight="median")["rules"])
    assert "médiane" in rules


def test_a_need_on_a_sunday_blocks_the_optimisation(import_dir):
    p = import_dir / "released_schedule.csv"
    d = pd.read_csv(p, encoding="utf-8-sig")
    i = d.index[d["schedule_quantity"] > 0][0]
    d.loc[i, "date"] = "2026-11-15"                      # a Sunday
    d.to_csv(p, index=False)
    s = analyse(str(import_dir))
    assert s["blocking"] and next(a for a in s["anomalies"] if a["code"] == "closed")["count"] == 1


def test_describe_and_columns(import_dir):
    info = describe(import_dir / "released_schedule.csv")
    assert info["first"] == "2026-11-09" and info["plants"] == ["PLT_001", "PLT_002", "PLT_003", "PLT_004"]
    bad = import_dir / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    try:
        check_columns("schedule", bad)
        assert False, "missing columns not detected"
    except ValueError as exc:
        assert "colonnes manquantes" in str(exc)


def pytest_approx(x):
    import pytest
    return pytest.approx(x, abs=0.05)
