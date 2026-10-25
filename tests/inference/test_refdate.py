# tests/inference/test_refdate.py
from datetime import date

from src.core.periods import Period
from src.inference._refdate import infer_reference_date, period_end


def test_explicit_text_date_wins_over_periods():
    periods = [Period("year", 2025)]
    assert infer_reference_date("As at 31 March 2024, the group", periods) == date(2024, 3, 31)
    assert infer_reference_date("Year ended March 2023", periods) == date(2023, 3, 31)


def test_latest_period_end_is_the_fallback():
    periods = [Period("year", 2022), Period("year", 2025, fiscal=True)]
    assert infer_reference_date("no date here", periods, fy_end_month=3) == date(2025, 3, 31)


def test_quarter_and_month_period_ends():
    assert period_end(Period("quarter", 2024, 2)) == date(2024, 6, 30)
    assert period_end(Period("month", 2024, 2)) == date(2024, 2, 29)
    assert period_end(Period("year", 2024)) == date(2024, 12, 31)


def test_no_text_and_no_periods_gives_none():
    assert infer_reference_date("", []) is None
