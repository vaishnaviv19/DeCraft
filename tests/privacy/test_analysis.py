# tests/privacy/test_analysis.py
import numpy as np
import pandas as pd

from src.contracts import SensitivityClass
from src.privacy._analysis import (LabelMap, detect_risk, exact_row_overlap, leaked_values,
                                   merge_risk, plan_table)


def test_rule_detector_overrides_an_llm_that_underrates_email():
    df = pd.DataFrame({"contact": ["a@x.com", "b@y.org", "c@z.net", "d@w.io", "e@q.co"]})
    plan = plan_table("t", df, {"contact": SensitivityClass.LOW_RISK}, {}, set(), set())
    assert plan[0].risk is SensitivityClass.DIRECT_IDENTIFIER
    assert plan[0].method == "regenerate" and plan[0].rule_label is SensitivityClass.DIRECT_IDENTIFIER


def test_llm_can_raise_risk_above_the_rules():
    df = pd.DataFrame({"notes": ["a", "b", "c"]})
    plan = plan_table("t", df, {"notes": SensitivityClass.SENSITIVE}, {}, set(), set())
    assert plan[0].risk is SensitivityClass.SENSITIVE and plan[0].noise == 0.12


def test_derived_columns_use_the_derived_method():
    df = pd.DataFrame({"Profit": [1.0, 2.0, 3.0], "Name": ["a", "b", "c"]})
    plan = {d.column: d for d in plan_table("t", df, {}, {}, {"Profit"}, set())}
    assert plan["Profit"].risk is SensitivityClass.DERIVED and plan["Profit"].method == "derived"
    assert plan["Name"].method == "regenerate"


def test_llm_derived_label_does_not_add_risk():
    assert merge_risk(SensitivityClass.DERIVED, SensitivityClass.LOW_RISK) is SensitivityClass.LOW_RISK


def test_unique_identifier_values_are_direct_identifiers():
    df = pd.DataFrame({"Ref": [f"A-{100 + i}" for i in range(10)]})
    assert detect_risk("Ref", df["Ref"])[0] is SensitivityClass.DIRECT_IDENTIFIER


def test_label_map_is_consistent_and_exempts_the_entity():
    rng = np.random.default_rng(2)
    labels = LabelMap(lambda r: f"X{r.integers(1000)}", rng, exempt={"Acme Corp"})
    assert labels.get("North") == labels.get("North") and labels.get("North") != "North"
    assert labels.get("Acme Corp") == "Acme Corp"


def test_no_source_identifier_is_reused():
    rng = np.random.default_rng(1)
    labels = LabelMap(lambda r: f"C-{r.integers(10000, 99999)}", rng)
    source = pd.Series(["A-101", "A-102", "A-103"])
    generated = source.map(labels.get)
    assert leaked_values(generated, source) == set() and generated.is_unique


def test_exempt_columns_are_preserved():
    df = pd.DataFrame({"Company": ["Acme", "Acme", "Acme"], "Revenue": [1.0, 2.0, 3.0]})
    plan = {d.column: d for d in plan_table("t", df, {}, {}, set(), {"Company"})}
    assert plan["Company"].exempt and plan["Company"].method == "preserve"
    assert not plan["Revenue"].exempt


def test_exact_row_overlap_is_zero_for_fresh_data():
    source = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    synthetic = pd.DataFrame({"a": [3, 4], "b": ["p", "q"]})
    assert exact_row_overlap(synthetic, source, ["a", "b"]) == 0
