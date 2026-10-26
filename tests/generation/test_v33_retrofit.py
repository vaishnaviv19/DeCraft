# tests/generation/test_v33_retrofit.py  -- v3.3 F-01 tiny tables, F-02 DCR, F-03 auto units
import numpy as np
import pandas as pd
import pytest

from src.contracts import Fact
from src.generation._tiny import jitter_table, shares_far_enough
from src.narrative._autounits import detect_scale_style, format_auto, unit_word_violations
from src.narrative._placeholders import TypedUnit, resolve
from src.privacy._dcr import dcr_violations, identical_match_share


def test_tiny_table_shares_move_at_least_10pct(cfg):
    src = pd.DataFrame({"Seg": list("ABCD"), "Rev": [420.0, 310.0, 180.0, 90.0]})
    for seed in range(20):
        out = jitter_table(src, ["Rev"], cfg.thresholds, np.random.default_rng(seed))
        s, g = src["Rev"] / src["Rev"].sum(), out["Rev"] / out["Rev"].sum()
        assert ((g / s - 1).abs() >= 0.10).all(), seed
        assert (((out["Rev"] - src["Rev"]).abs() / src["Rev"]) >= 0.05).all()


def test_old_single_factor_design_would_leak_shares():
    src = np.array([420.0, 310.0, 180.0, 90.0])
    assert not shares_far_enough(src, src * 1.3, 0.10)


def test_dcr_flags_scaled_copy_through_shares():
    src = pd.DataFrame({"Seg": ["A", "B", "C"], "Rev": [100.0, 200.0, 300.0]})
    scaled_copy = pd.DataFrame({"Seg": ["A", "B", "C"], "Rev": [101.0, 202.0, 303.0]})
    assert dcr_violations("t", src, scaled_copy, ["Rev"], ["Seg"], 0.05)
    fresh = pd.DataFrame({"Seg": ["X", "Y", "Z"], "Rev": [160.0, 90.0, 420.0]})
    assert not dcr_violations("t", src, fresh, ["Rev"], ["Seg"], 0.05)
    assert identical_match_share(src, src, ["Seg", "Rev"]) == 1.0


def test_auto_units():
    assert format_auto(4.5e9, "USD") == "$4.5 billion"
    assert format_auto(1.25e10, "INR", "indian") == "₹1,250 crore"
    assert format_auto(12.44, "%") == "12.4%"
    assert format_auto(3200, "units") == "3,200 units"
    assert format_auto(2.0e6, "EUR", "western_short") == "€2m"


def test_auto_placeholder_uses_the_fact_scale_and_the_source_style():
    """Facts are in the table's units ('Rs cr'); |auto takes them back to base units."""
    facts = {"sales.latest": Fact(key="sales.latest", value=87627.1, kind="number", source="t", unit="₹",
                                  decimals=1, scale=1e7)}
    assert resolve("Sales reached {{sales.latest|auto}}.", facts, "indian") == "Sales reached ₹87,627.1 crore."


def test_typed_scale_words_are_rejected():
    assert unit_word_violations("Revenue reached {{rev.total|auto}} billion") == ["}} billion"]
    assert unit_word_violations("Revenue was ${{rev.total|auto}}")
    assert unit_word_violations("Revenue was {{rev.total|auto}}, up from last year") == []
    facts = {"rev.total": Fact(key="rev.total", value=4.5e9, kind="number", source="t", unit="$")}
    with pytest.raises(TypedUnit):
        resolve("Revenue reached {{rev.total|auto}} million.", facts)


def test_scale_style_follows_the_source():
    assert detect_scale_style("Revenue of ₹1,200 crore and EBITDA of ₹300 crore") == "indian"
    assert detect_scale_style("EBITDA rose to Rs. 3,215cr") == "indian"
    assert detect_scale_style("Sales of $2.1bn and profit of $300mn") == "western_short"
    assert detect_scale_style("no numbers here") == "western"
