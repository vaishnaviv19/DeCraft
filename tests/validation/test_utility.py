# tests/validation/test_utility.py
import pandas as pd

from src.contracts import Severity
from src.validation._utility import (category_share_warnings, correlation_strength_warning,
                                     pattern_gate, range_warnings)


def test_a_confirmed_trend_that_flips_is_an_error(cfg):
    found = pattern_gate("p1", "up", "down", source_is_confirmed=True)
    assert len(found) == 1 and found[0].severity is Severity.ERROR
    assert pattern_gate("p1", "up", "up", True) == []
    assert pattern_gate("p1", "up", "down", source_is_confirmed=False) == []


def test_correlation_strength_drift_is_a_warning(cfg):
    found = correlation_strength_warning("p1", 0.9, 0.3, cfg.thresholds)
    assert len(found) == 1 and found[0].severity is Severity.WARNING
    assert correlation_strength_warning("p1", 0.9, 0.85, cfg.thresholds) == []


def test_range_warnings(cfg):
    source = pd.Series([0.0, 100.0])
    assert range_warnings("t", "v", source, pd.Series([-100.0, 100.0]), cfg.thresholds)
    assert range_warnings("t", "v", source, pd.Series([10.0, 90.0]), cfg.thresholds) == []


def test_category_share_warnings(cfg):
    source = pd.Series(["A"] * 8 + ["B"] * 2)
    drifted = pd.Series(["A"] * 2 + ["B"] * 8)
    assert category_share_warnings("t", "c", source, drifted, cfg.thresholds)
    assert category_share_warnings("t", "c", source, source, cfg.thresholds) == []
