# tests/core/test_periods.py
import pytest

from src.core.periods import (Period, cumulate, decumulate, detect_axis, detect_cumulative,
                              fiscal_year_end_month, parse_period, verify_yoy, yoy_pct)


def test_parse_quarters_months_and_years():
    q = parse_period("Q1FY24")
    assert (q.kind, q.year, q.sub, q.fiscal) == ("quarter", 2024, 1, True)
    m = parse_period("Mar 2023")
    assert (m.kind, m.year, m.sub, m.fiscal) == ("month", 2023, 3, False)
    y = parse_period("FY21A")
    assert (y.kind, y.year, y.fiscal) == ("year", 2021, True)
    assert parse_period("FY21A").ordinal < parse_period("FY24A").ordinal
    assert parse_period("not a period") is None


def test_axis_regularity_and_gaps():
    labels = ["FY21A", "FY22A", "FY23A", "FY24E"]
    ps, kind, regular = detect_axis(labels)
    assert kind == "year" and regular is True and len(ps) == 4
    _, _, gapped = detect_axis(["FY21A", "FY22A", "FY24A"])
    assert gapped is False
    assert detect_axis(["Revenue", "Cost"]) is None


def test_fiscal_year_end_month_from_text():
    assert fiscal_year_end_month("Year ended March 2023") == 3
    assert fiscal_year_end_month("as at 31 December 2022") is None


def test_cumulative_round_trip():
    flow = [10.0, 12.0, 8.0, 11.0, 9.0, 14.0]
    ps = [Period("quarter", 2023, i + 1) for i in range(4)] + [Period("quarter", 2024, i + 1) for i in range(2)]
    cum = cumulate(flow, ps)
    assert cum[:4] == [10.0, 22.0, 30.0, 41.0] and cum[4] == 9.0
    assert detect_cumulative(cum, ps) is True
    assert decumulate(cum, ps) == flow
    assert detect_cumulative(flow, ps) is False
    assert detect_cumulative([1, 2, 3], ps[:3], header="Revenue YTD") is True


def test_yoy_verification():
    levels = [100.0, 110.0, 121.0, 133.1]
    pct = [None, 10.0, 10.0, 10.0]
    assert yoy_pct(levels, 1) == pytest.approx(pct)
    rate, n = verify_yoy(levels, pct, lag=1)
    assert rate == 1.0 and n == 3
    assert verify_yoy(levels[:2], pct[:2], lag=1) == (None, 1)
