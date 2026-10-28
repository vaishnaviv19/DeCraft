# tests/inference/test_totals.py
import pandas as pd

from src.inference._formula import check_formula, parse_formula
from src.inference._totals import additive_columns, detect_total_columns, total_row_links


def verify_formula(df, spec, decimals=None, rel_tol=0.005, min_rows=5):
    """(Match Rate | None, rows tested, invalid rows) from the pipeline's check_formula."""
    r = check_formula(df, spec, decimals, rel_tol, min_rows)
    return r.match_rate, r.n_rows, r.n_invalid


def test_total_column_is_found_and_verifies_without_the_llm():
    df = pd.DataFrame({"Q1": [1.0] * 6, "Q2": [2.0] * 6, "Q3": [3.0] * 6, "Total": [6.0] * 6})
    texts = detect_total_columns(df)
    assert texts == ["[Total] = sum([Q1], [Q2], [Q3])"]
    assert verify_formula(df, parse_formula(texts[0], list(df.columns)))[0] == 1.0


def test_no_total_header_means_no_formula():
    assert detect_total_columns(pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})) == []


def test_total_row_links_and_additive_columns():
    df = pd.DataFrame({"Item": ["A", "B", "Total"], "Value": [1.0, 2.0, 3.0]})
    links = total_row_links(df, "Item")
    assert links == {2: [0, 1]}
    assert additive_columns(df, links)["Value"] is True
    bad = pd.DataFrame({"Item": ["A", "B", "Total"], "Value": [1.0, 2.0, 4.0]})
    assert additive_columns(bad, total_row_links(bad, "Item"))["Value"] is False


def test_additive_is_unknown_without_total_rows():
    df = pd.DataFrame({"Item": ["A", "B"], "Value": [1.0, 2.0]})
    assert additive_columns(df, total_row_links(df, "Item"))["Value"] is None


def test_aggregate_labels_are_not_total_rows():
    """'Total Assets' and 'Total asset T.O' cannot be linked without indent levels."""
    df = pd.DataFrame({"Item": ["Cash", "Debt", "Total Assets", "Total asset T.O (x)"],
                       "Value": [1.0, 2.0, 3.0, 0.5]})
    assert total_row_links(df, "Item") == {}
