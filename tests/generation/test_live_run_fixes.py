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


def test_dates_print_in_the_source_style():
    from src.core.dates import date_style, format_date_like
    style = date_style(["14-May-21", "2-Aug-22", "11-Sep-23"])
    assert format_date_like(pd.Timestamp("2022-03-05"), style) == "5-Mar-22"
    assert format_date_like(pd.Timestamp("2022-03-05"), date_style(["2024-03-31"])) == "2022-03-05"


def test_generated_dates_keep_the_source_time_direction():
    import numpy as np
    from src.config import load_config
    from src.contracts import SourceDocument, SourceTable
    from src.generation import generate
    from src.inference import infer
    from src.privacy import plan_privacy
    cfg = load_config(llm_enabled=False)
    rows = [[d, str(v)] for d, v in zip(["14-May-21", "10-Aug-21", "17-Feb-22", "18-May-22", "2-Aug-22",
                                         "20-Feb-23", "15-May-23", "11-Sep-23"], [120, 161, 263, 255, 246, 223, 224, 301])]
    doc = SourceDocument(document_sha256="dates123", tables=[SourceTable(table_id="h", page=1, header=["Dates", "Target"],
                                                                          raw_cells=rows)], text_blocks=["x"])
    report = infer(doc, cfg)
    frame = generate(doc, report, plan_privacy(doc, report, cfg), cfg, np.random.default_rng(2)).tables["h"]
    dates = list(frame["Dates"])
    assert all(d is not None and not pd.isna(d) for d in dates) and dates == sorted(dates)


def test_caption_check_ignores_dash_variants_from_the_pdf_renderer():
    from src.rendering._qa import _squash
    assert _squash("Net inc. + Depn., C.F ‑ Finance") == _squash("Net inc. + Depn., C.F - Finance")
