# tests/core/test_missingness.py
from src.core.missingness import (dash_means_zero, token_kind)


def test_token_kinds():
    assert token_kind(None) == "null"
    assert token_kind("") == "null"
    assert token_kind("  ") == "null"
    assert token_kind("-") == "dash"
    assert token_kind("—") == "dash"
    assert token_kind("N/A") == "na"
    assert token_kind("n.m.") == "nm"
    assert token_kind("12.5") is None


def test_dash_means_zero_only_when_totals_need_it():
    links = {2: [0, 1]}
    assert dash_means_zero(["-", "10", "10"], links) is True
    assert dash_means_zero(["5", "10", "15"], links) is False
