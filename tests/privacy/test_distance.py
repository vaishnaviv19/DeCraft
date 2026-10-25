# tests/privacy/test_distance.py
import pandas as pd

from src.contracts import Severity
from src.privacy._distance import (headline_violations, quasi_row_matches, rare_combination_violations,
                                   rel_diff, value_distance_violations)


def test_value_distance_flags_cells_close_to_the_source():
    source = pd.DataFrame({"id": ["A-1", "A-2"], "v": [100.0, 200.0]})
    synthetic = pd.DataFrame({"id": ["A-1", "A-2"], "v": [101.0, 300.0]})
    found = value_distance_violations("t", source, synthetic, "id", ["v"], 0.05)
    assert len(found) == 1 and found[0].rows == [0]
    assert found[0].severity is Severity.ERROR and found[0].repairable


def test_value_distance_passes_when_every_cell_is_far_enough():
    source = pd.DataFrame({"id": ["A-1", "A-2"], "v": [100.0, 200.0]})
    synthetic = pd.DataFrame({"id": ["A-1", "A-2"], "v": [120.0, 260.0]})
    assert value_distance_violations("t", source, synthetic, "id", ["v"], 0.05) == []


def test_quasi_identifier_row_match_is_an_error():
    source = pd.DataFrame({"city": ["A", "B"], "age": [30, 40], "v": [1.0, 2.0]})
    synthetic = pd.DataFrame({"city": ["A", "C"], "age": [30, 50], "v": [9.0, 9.0]})
    found = quasi_row_matches("t", source, synthetic, ["city", "age"])
    assert len(found) == 1 and found[0].rows == [0]


def test_headline_distance():
    found = headline_violations({"t.Revenue:sum": 1000.0}, {"t.Revenue:sum": 1010.0}, 0.10)
    assert len(found) == 1 and found[0].check == "headline_distance"
    assert headline_violations({"t.Revenue:sum": 1000.0}, {"t.Revenue:sum": 1200.0}, 0.10) == []
    assert rel_diff(1200.0, 1000.0) == 0.2


def test_no_k_anonymity_under_twenty_rows(cfg):
    small = pd.DataFrame({"city": ["A"] * 5, "age": [30] * 5})
    assert rare_combination_violations("t", small, ["city", "age"], cfg.thresholds) == []


def test_rare_combination_is_a_warning_from_twenty_rows(cfg):
    big = pd.DataFrame({"city": ["A"] * 19 + ["B"], "age": [30] * 19 + [71]})
    found = rare_combination_violations("t", big, ["city", "age"], cfg.thresholds)
    assert len(found) == 1 and found[0].severity is Severity.WARNING
