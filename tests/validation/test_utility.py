# tests/validation/test_utility.py
import pandas as pd

from src.validation._utility import (category_share_warnings, range_warnings)


def test_range_warnings(cfg):
    source = pd.Series([0.0, 100.0])
    assert range_warnings("t", "v", source, pd.Series([-100.0, 100.0]), cfg.thresholds)
    assert range_warnings("t", "v", source, pd.Series([10.0, 90.0]), cfg.thresholds) == []


def test_category_share_warnings(cfg):
    source = pd.Series(["A"] * 8 + ["B"] * 2)
    drifted = pd.Series(["A"] * 2 + ["B"] * 8)
    assert category_share_warnings("t", "c", source, drifted, cfg.thresholds)
    assert category_share_warnings("t", "c", source, source, cfg.thresholds) == []
