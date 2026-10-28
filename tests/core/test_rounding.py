# tests/core/test_rounding.py
import pandas as pd

from src.core.rounding import build_display_df, display_round, largest_remainder


def test_display_round_is_half_up_not_bankers():
    assert display_round(2.5, 0) == 3.0
    assert display_round(1.005, 2) == 1.01
    assert display_round(-2.5, 0) == -3.0
    assert display_round(1.234, 2) == 1.23


def test_largest_remainder_keeps_the_total():
    got = largest_remainder([33.33, 33.33, 33.34], total=100.0, decimals=1)
    assert round(sum(got), 1) == 100.0 and got == [33.3, 33.3, 33.4]


def test_build_display_df_recomputes_totals_after_rounding():
    full = pd.DataFrame({"Item": ["A", "B", "Total"], "V": [1.04, 2.04, 3.08]})
    disp = build_display_df(full, {"V": 1}, {2: [0, 1]}, mode="sum_of_displayed")
    assert list(disp["V"]) == [1.0, 2.0, 3.0]
    true_total = build_display_df(full, {"V": 1}, {2: [0, 1]}, mode="true_total")
    assert list(true_total["V"]) == [1.0, 2.0, 3.1]
