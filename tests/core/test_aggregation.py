# tests/core/test_aggregation.py
import pandas as pd

from src.core.aggregation import apply_aggregate_rule, detect_aggregate_rules, detect_scalar_aggregate


def test_summary_total_is_detected_as_an_aggregate_of_the_detail():
    detail = pd.DataFrame({"Region": ["N", "N", "S", "S", "E", "E"],
                           "Revenue": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]})
    summary = pd.DataFrame({"Region": ["N", "S", "E"], "Total Revenue": [30.0, 70.0, 110.0]})
    rules = detect_aggregate_rules({"detail": detail, "summary": summary})
    assert len(rules) == 1
    rule = rules[0]
    assert (rule.detail, rule.summary, rule.agg) == ("detail", "summary", "sum")
    assert rule.detail_col == "Revenue" and rule.summary_col == "Total Revenue"
    applied = apply_aggregate_rule(rule, detail, summary)
    assert list(applied["Total Revenue"]) == [30.0, 70.0, 110.0]


def test_unrelated_tables_produce_no_rule():
    a = pd.DataFrame({"Region": ["N", "S", "E"], "Revenue": [1.0, 2.0, 3.0]})
    b = pd.DataFrame({"Region": ["N", "S", "E"], "Revenue": [9.0, 8.0, 7.0]})
    assert detect_aggregate_rules({"a": a, "b": b}) == []


def test_scalar_aggregate_finds_a_headline_number():
    detail = pd.DataFrame({"Revenue": [10.0, 20.0, 30.0]})
    assert detect_scalar_aggregate(detail, "Revenue", 60.0) == "sum"
    assert detect_scalar_aggregate(detail, "Revenue", 20.0) == "mean"
    assert detect_scalar_aggregate(detail, "Revenue", 7.0) is None
