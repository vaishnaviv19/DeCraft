# tests/core/test_periods.py

from src.core.periods import (fiscal_year_end_month, parse_period)


def test_parse_quarters_months_and_years():
    q = parse_period("Q1FY24")
    assert (q.kind, q.year, q.sub, q.fiscal) == ("quarter", 2024, 1, True)
    m = parse_period("Mar 2023")
    assert (m.kind, m.year, m.sub, m.fiscal) == ("month", 2023, 3, False)
    y = parse_period("FY21A")
    assert (y.kind, y.year, y.fiscal) == ("year", 2021, True)
    assert parse_period("FY21A").ordinal < parse_period("FY24A").ordinal
    assert parse_period("not a period") is None


def test_fiscal_year_end_month_from_text():
    assert fiscal_year_end_month("Year ended March 2023") == 3
    assert fiscal_year_end_month("as at 31 December 2022") is None


