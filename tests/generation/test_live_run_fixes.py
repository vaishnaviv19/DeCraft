# tests/generation/test_live_run_fixes.py  (found in the first live LLM run, 2026-10-26)
import pandas as pd
import pytest

from src.core.dates import to_calendar_dates
from src.generation._apply import headline_push


@pytest.mark.parametrize("text,iso", [("14-May-21", "2021-05-14"), ("2-Aug-22", "2022-08-02"),
                                      ("11 Sept 23", "2023-09-11"), ("5-May-2021", "2021-05-05")])
def test_month_name_dates_with_short_years_are_read(text, iso):
    assert to_calendar_dates(pd.Series([text]))[0].iloc[0] == pd.Timestamp(iso)


@pytest.mark.parametrize("text", ["31-Feb-21", "03/04/21", "May-21", "21"])
def test_impossible_ambiguous_or_partial_dates_are_still_refused(text):
    assert pd.isna(to_calendar_dates(pd.Series([text]))[0].iloc[0])


def test_the_total_push_cannot_undo_the_largest_value_push():
    source = [120, 161, 263, 255, 246, 223, 224, 301]
    generated = [143, 191, 314, 301, 303, 265, 271, 285]       # largest 314 vs 301: only 4% apart
    out = headline_push(generated, source, list(range(8)), 0.10, 0.05)
    assert abs(max(out) - 301) / 301 >= 0.10
    assert abs(sum(out) - sum(source)) / sum(source) >= 0.10
    assert all(abs(o - s) / s >= 0.05 for o, s in zip(out, source) if o != generated[source.index(s)])


def test_a_column_close_on_both_headlines_is_moved_on_both():
    source = [100.0, 200.0, 300.0]
    out = headline_push([104.0, 196.0, 305.0], source, [0, 1, 2], 0.10, 0.05)
    assert abs(max(out) - 300) / 300 >= 0.10 and abs(sum(out) - 600) / 600 >= 0.10
