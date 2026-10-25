# tests/validation/test_repair_fixes.py
"""Validation and repair: summary tables with Total rows, rule outputs that go negative, repair that
touches only the flagged cells, a non-reversible distance push, trend / ordering / key repairs, and
category-share warnings that understand fake labels."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.contracts import (DecisionStatus, Enforcement, Rule, RuleKind, RuleTier, Severity, SourceDocument,
                           SourceTable, ValidationReport, Violation)
from src.contracts.dataset import empty_dataset
from src.core.aggregation import AggregateRule, apply_aggregate_rule
from src.generation import generate
from src.inference import infer
from src.privacy import plan_privacy
from src.validation import repair_dataset
from src.validation._utility import category_share_warnings
from tests.conftest import FakeLLM

CFG = load_config(llm_enabled=False)


def violation(check, table="m", column="v", rows=(), rule_id=None):
    return ValidationReport(ok=False, violations=[Violation(check=check, severity=Severity.ERROR, table_id=table,
                                                            column=column, rows=list(rows), rule_id=rule_id,
                                                            message="x", repairable=True)])


def dataset(**tables):
    data = empty_dataset()
    data.tables = {k: pd.DataFrame(v) for k, v in tables.items()}
    return data


def no_inference(rules=()):
    return SimpleNamespace(rules=list(rules), profiles=[], relationships=[])


# ---------------------------------------------------------------- A. summary table with a Total row
DETAIL = SourceTable(table_id="sales", page=1, header=["Region", "Amount"], raw_cells=[
    [r, str(a)] for r, a in zip(["North", "North", "South", "South", "East", "East", "West", "West"],
                                [10, 20, 30, 40, 50, 60, 70, 80])])
SUMMARY = SourceTable(table_id="by_region", page=2, header=["Region", "Total Amount"], raw_cells=[
    ["North", "30"], ["South", "70"], ["East", "110"], ["West", "150"], ["Total", "360"]])


@pytest.mark.parametrize("seed", range(5))
def test_summary_with_a_total_row_generates_and_adds_up(seed):
    doc = SourceDocument(document_sha256="val12345", tables=[DETAIL, SUMMARY], text_blocks=["x"])
    report = infer(doc, CFG)
    data = generate(doc, report, plan_privacy(doc, report, CFG), CFG, np.random.default_rng(seed))
    s = data.tables["by_region"].set_index("Region")["Total Amount"]
    assert s["Total"] == pytest.approx(s.drop("Total").sum()) and data.validation.ok


def test_the_aggregate_step_leaves_total_rows_to_the_roll_up():
    rule = AggregateRule("d", "s", "g", "g", "x", "y", "sum", 1.0, 3)
    detail = pd.DataFrame({"g": ["A", "A", "B"], "x": [1.0, 2.0, 5.0]})
    summary = pd.DataFrame({"g": ["A", "B", "C", "Total"], "y": [0.0, 0.0, 0.0, 99.0]})
    assert apply_aggregate_rule(rule, detail, summary)["y"].tolist() == [3.0, 5.0, 0.0, 99.0]


# ---------------------------------------------------------------- B. a rule output may go negative
def test_thin_margins_no_longer_crash_generation(gateway_for):
    rows = [[f"A-{i}", str(r), str(c), str(r - c)] for i, (r, c) in enumerate(
        [(100, 95), (120, 112), (90, 86), (150, 141), (130, 124), (110, 104), (95, 90), (105, 99)])]
    t = SourceTable(table_id="p", page=1, header=["Ref", "Revenue", "Cost", "Profit"], raw_cells=rows)
    reply = json.dumps({"domain": "x", "domain_confidence": 0.5, "columns": [], "proposals": [
        {"kind": "formula", "columns": ["Profit", "Revenue", "Cost"],
         "params": {"expression": "[Profit] = [Revenue] - [Cost]"}}]})
    cfg = CFG.model_copy(update={"llm_enabled": True})
    for seed in range(10):
        doc = SourceDocument(document_sha256="val12345", tables=[t], text_blocks=["x"])
        report = infer(doc, cfg, gateway=gateway_for(FakeLLM([reply])))
        frame = generate(doc, report, plan_privacy(doc, report, cfg), cfg, np.random.default_rng(seed)).tables["p"]
        assert np.allclose(frame["Profit"], frame["Revenue"] - frame["Cost"])   # the rule wins


# ---------------------------------------------------------------- C. repair only the flagged cells
def test_invalid_number_repair_keeps_real_blanks():
    fixed, _, _ = repair_dataset(dataset(m={"v": [np.nan, 2.0, 3.0, np.nan]}),
                                 violation("invalid_number", rows=[0]), CFG)
    v = fixed.tables["m"]["v"]
    assert v.iloc[0] == 2.5 and np.isnan(v.iloc[3])


def test_negative_repair_touches_only_flagged_rows():
    fixed, _, _ = repair_dataset(dataset(m={"v": [-1.0, -2.0, 3.0]}), violation("negative_value", rows=[0]), CFG)
    assert fixed.tables["m"]["v"].tolist() == [0.0, -2.0, 3.0]


# ---------------------------------------------------------------- D. distance push cannot be reversed
def test_distance_repair_uses_random_steps_that_hide_the_source():
    source = {"m": pd.DataFrame({"v": [100.0, 250.0, 333.0, 410.0, 520.0]})}
    fixed, _, _ = repair_dataset(dataset(m={"v": [101.0, 251.0, 334.0, 411.0, 521.0]}),
                                 violation("value_distance", rows=range(5)), CFG, frames=source)
    ratios = (fixed.tables["m"]["v"] / source["m"]["v"]).round(6)
    assert ratios.nunique() == 5                                  # no single factor to divide out
    assert ((ratios - 1).abs() >= 0.05).all() and ((ratios - 1).abs() <= 0.16).all()


# ---------------------------------------------------------------- E. a flipped trend is re-ordered
def test_flipped_trend_is_repaired_by_reordering():
    source = {"m": pd.DataFrame({"v": [10.0, 12.0, 15.0, 19.0, 24.0, 30.0]})}
    fixed, action, _ = repair_dataset(dataset(m={"v": [33.0, 26.0, 21.0, 17.0, 13.0, 11.0]}),
                                      violation("utility_direction"), CFG, frames=source)
    assert fixed.tables["m"]["v"].tolist() == [11.0, 13.0, 17.0, 21.0, 26.0, 33.0]
    assert "trend" in action


# ---------------------------------------------------------------- F. ordering rules are repaired
def test_ordering_violations_are_repaired():
    rule = Rule(rule_id="o1", kind=RuleKind.ORDERING, table_id="m", columns=["start", "end"],
                params={"min_gap_days": 2}, tier=RuleTier.STRONGLY_INFERRED, status=DecisionStatus.ACCEPTED,
                enforcement=Enforcement.HARD, reason="x")
    data = dataset(m={"start": pd.to_datetime(["2024-01-10", "2024-02-01"]),
                      "end": pd.to_datetime(["2024-01-01", "2024-03-01"])})
    fixed, _, _ = repair_dataset(data, violation("ordering", column="end", rows=[0], rule_id="o1"), CFG,
                                 inference=no_inference([rule]))
    assert fixed.tables["m"]["end"].tolist() == list(pd.to_datetime(["2024-01-12", "2024-03-01"]))


# ---------------------------------------------------------------- G. warnings understand fake labels
def test_category_share_compares_shape_when_labels_were_replaced():
    th = CFG.thresholds
    source = pd.Series(["North"] * 6 + ["South"] * 3 + ["East"])
    fake_same_shape = pd.Series(["Zeta"] * 6 + ["Omega"] * 3 + ["Kappa"])
    assert category_share_warnings("t", "c", source, fake_same_shape, th) == []
    fake_other_shape = pd.Series(["Zeta"] * 2 + ["Omega"] * 4 + ["Kappa"] * 4)
    assert category_share_warnings("t", "c", source, fake_other_shape, th)


# ---------------------------------------------------------------- H. only broken keys are replaced
def test_key_repair_replaces_only_missing_and_repeated_keys():
    data = dataset(m={"id": ["A-101", "A-102", "A-101", None, "A-105"]})
    fixed, _, _ = repair_dataset(data, violation("primary_key", column="id"), CFG)
    keys = fixed.tables["m"]["id"].tolist()
    assert keys[0] == "A-101" and keys[1] == "A-102" and keys[4] == "A-105"   # links to these stay valid
    assert len(set(keys)) == 5 and all(k.startswith("A-1") for k in keys)
