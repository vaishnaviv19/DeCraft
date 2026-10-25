# tests/core/test_missingness.py
import numpy as np
import pandas as pd
from src.core.missingness import (MissingProfile, apply_missingness, dash_means_zero, profile_missing,
                                  token_kind)


def test_token_kinds():
    assert token_kind(None) == "null"
    assert token_kind("") == "null"
    assert token_kind("  ") == "null"
    assert token_kind("-") == "dash"
    assert token_kind("—") == "dash"
    assert token_kind("N/A") == "na"
    assert token_kind("n.m.") == "nm"
    assert token_kind("12.5") is None


def test_no_missing_pattern():
    p = profile_missing(pd.DataFrame({"v": ["1", "2", "3"]}), "v")
    assert (p.rate, p.pattern, p.kind) == (0.0, "none", "null")


def test_leading_pattern():
    p = profile_missing(pd.DataFrame({"v": ["", "", "3", "4"]}), "v")
    assert p.pattern == "leading" and p.leading == 2 and p.rate == 0.5


def test_grouped_pattern():
    df = pd.DataFrame({"v": ["1", "", "", "2", "3", "4"], "g": ["A", "A", "A", "B", "B", "B"]})
    p = profile_missing(df, "v", group_cols=("g",))
    assert p.pattern == "grouped" and p.group_col == "g"
    assert p.group_rates["A"] == 2 / 3


def test_random_pattern():
    p = profile_missing(pd.DataFrame({"v": ["1", "", "3", "4"]}), "v")
    assert p.pattern == "random" and p.kind == "null"


def test_apply_missingness_reproduces_pattern():
    rng = np.random.default_rng(0)
    prof = MissingProfile(0.5, "dash", "leading", leading=2, n_source=4)
    out = apply_missingness(pd.Series([1.0, 2.0, 3.0, 4.0]), prof, None, rng)
    assert list(out) == ["-", "-", 3.0, 4.0]


def test_dash_means_zero_only_when_totals_need_it():
    links = {2: [0, 1]}
    assert dash_means_zero(["-", "10", "10"], links) is True
    assert dash_means_zero(["5", "10", "15"], links) is False
