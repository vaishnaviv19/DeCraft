# tests/generation/test_pattern_fixes.py
"""Patterns: statements read row by row, time order everywhere, blanks kept, one correlation step
before formulas, flipped statement trends repaired, and LLM break causes kept only when quoted."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.contracts import (ColumnRef, Direction, PatternKind, PatternSpec, SegmentOut, SourceDocument,
                           SourceTable)
from src.contracts.dataset import empty_dataset
from src.core.periods import time_order
from src.core.stats import classify_trend
from src.generation import generate
from src.inference import infer
from src.inference._patterns import explain_breaks
from src.privacy import plan_privacy
from src.validation import repair_dataset
from src.validation._checks import check_patterns
from tests.conftest import FakeLLM

CFG = load_config(llm_enabled=False)
TH = CFG.thresholds


def run(*tables, seed=1, cfg=CFG, gateway=None):
    doc = SourceDocument(document_sha256="pat12345", tables=list(tables), text_blocks=["x"])
    report = infer(doc, cfg, gateway=gateway)
    return report, generate(doc, report, plan_privacy(doc, report, cfg), cfg, np.random.default_rng(seed))


def series_table(table_id, column, values, start=10):
    return SourceTable(table_id=table_id, page=1, header=["Year", column],
                       raw_cells=[[f"FY{start + i}", "" if v is None else str(v)] for i, v in enumerate(values)])


PL = SourceTable(table_id="pl", page=1, header=["Line item", "FY25", "FY24", "FY23", "FY22", "FY21"], raw_cells=[
    ["Revenue", "1350", "1260", "1170", "1080", "1000"], ["Cost of sales", "770", "730", "690", "640", "600"],
    ["Gross profit", "580", "530", "480", "440", "400"], ["Total", "2700", "2520", "2340", "2160", "2000"]])
YEARS = ["FY21", "FY22", "FY23", "FY24", "FY25"]


# ---------------------------------------------------------------- 1. statements, row by row
def test_statement_line_items_are_time_series_read_in_period_order():
    report, _ = run(PL)
    revenue = next(p for p in report.patterns if p.row_label == "Revenue" and p.kind is PatternKind.TREND)
    assert revenue.direction is Direction.UP and revenue.period_columns == YEARS   # newest-first header sorted
    assert revenue.fields[0].column == "Line item"


@pytest.mark.parametrize("seed", range(8))
def test_statement_trends_survive_generation(seed):
    _, data = run(PL, seed=seed)
    frame = data.tables["pl"].set_index("Line item")
    for item in ("Revenue", "Cost of sales", "Gross profit"):
        assert classify_trend(frame.loc[item, YEARS].astype(float).tolist(), TH)[0] == "up", item
    assert data.validation.ok


# ---------------------------------------------------------------- 2. time order everywhere
def test_newest_first_rows_keep_their_trend_over_time():
    years = [f"FY{y}" for y in range(25, 15, -1)]
    t = SourceTable(table_id="r", page=1, header=["Year", "Revenue"], raw_cells=[
        [y, str(v)] for y, v in zip(years, [200, 188, 171, 160, 149, 140, 128, 119, 110, 100])])
    _, data = run(t)
    frame = data.tables["r"]
    in_time = [frame["Revenue"].iloc[i] for i in time_order(frame["Year"])]
    assert classify_trend(in_time, TH)[0] == "up" and data.validation.ok


def test_time_order_sorts_periods_and_dates():
    assert time_order(["FY25", "FY23", "FY24"]) == [1, 2, 0]
    assert time_order(["2024-03-01", "2023-01-01", None]) == [1, 0, 2]


# ---------------------------------------------------------------- 3. blanks stay blank
@pytest.mark.parametrize("values", [
    [100, 110, 121, 133, None, 161, 177, 195, 214, 236, 260, 286],                         # trend
    [100, 110, 120, 130, 140, 150, 90, 80, 70, 60, None, 40, 50, 60, 70, 80],              # segments
])
def test_a_blank_inside_a_shaped_series_stays_blank(values):
    _, data = run(series_table("b", "Sales", values))
    generated = data.tables["b"]["Sales"]
    assert generated.isna().tolist() == [v is None for v in values]


# ---------------------------------------------------------------- 4. one correlation step, before formulas
def test_correlated_pair_is_generated_before_formulas_and_keeps_blanks(gateway_for):
    rng = np.random.default_rng(0)
    n = 14
    x = np.cumsum(rng.normal(10, 3, n)) + 100
    y = x * 2 + rng.normal(0, 4, n)
    rows = [[f"FY{10 + i}", f"{x[i]:.1f}", "" if i == 5 else f"{y[i]:.1f}",
             "" if i == 5 else f"{x[i] + y[i]:.1f}"] for i in range(n)]
    t = SourceTable(table_id="c", page=1, header=["Year", "Units", "Cost", "Total cost"], raw_cells=rows)
    cfg = CFG.model_copy(update={"llm_enabled": True})
    reply = json.dumps({"domain": "x", "domain_confidence": 0.5, "columns": [], "proposals": [
        {"kind": "formula", "columns": ["Total cost", "Units", "Cost"],
         "params": {"expression": "[Total cost] = [Units] + [Cost]"}}]})
    report, data = run(t, cfg=cfg, gateway=gateway_for(FakeLLM([reply])))
    assert any(p.kind is PatternKind.CORRELATION for p in report.patterns)
    frame = data.tables["c"]
    assert np.isnan(frame["Cost"].iloc[5]) and np.isnan(frame["Total cost"].iloc[5])
    both = frame.dropna()
    assert np.allclose(both["Total cost"], both["Units"] + both["Cost"])      # no stale formula
    assert all(float(v * 10).is_integer() for v in both["Units"])            # rounded like the source
    assert data.validation.repairs == []                                      # nothing to repair


# ---------------------------------------------------------------- 5. flipped statement trend repaired
def test_flipped_line_item_trend_is_found_and_reordered():
    report, _ = run(PL)
    data = empty_dataset()
    data.tables = {"pl": pd.DataFrame({"Line item": ["Revenue", "Cost of sales", "Gross profit", "Total"],
                                       **{y: [1500.0 - 60 * k, 700.0, 520.0, 2720.0] for k, y in enumerate(YEARS)}})}
    frames = {"pl": pd.DataFrame({"Line item": ["Revenue", "Cost of sales", "Gross profit", "Total"],
                                  **{y: [1000.0 + 90 * k, 600.0, 400.0, 2000.0] for k, y in enumerate(YEARS)}})}
    flips = [v for v in check_patterns(data, report, CFG, frames) if v.rule_id == "pl.row:Revenue.trend"]
    assert flips and flips[0].rows == [0]
    fixed, action, _ = repair_dataset(data, SimpleNamespace(violations=flips), CFG, inference=report, frames=frames)
    revenue = fixed.tables["pl"].loc[0, YEARS].astype(float).tolist()
    assert revenue == sorted(revenue) and "Revenue" in action


# ---------------------------------------------------------------- 6. break causes must be quoted
def _break():
    return PatternSpec(pattern_id="t.Sales.segments", kind=PatternKind.SEGMENTS,
                       fields=[ColumnRef(table_id="t", column="Sales")],
                       time_field=ColumnRef(table_id="t", column="Year"), direction=Direction.MIXED, n=9,
                       segments=[SegmentOut(start=0, end=4, direction="up", pct_change=20),
                                 SegmentOut(start=5, end=8, direction="down", pct_change=-30)],
                       tier="confirmed", uncertain=False)


@pytest.mark.parametrize("quote,kept", [("stores were closed during the 2020 lockdown", True),
                                        ("demand collapsed because of a new competitor", False)])
def test_a_break_cause_is_kept_only_when_the_text_says_it(gateway_for, quote, kept):
    pattern = _break()
    reply = json.dumps({"causes": [{"pattern_id": pattern.pattern_id, "cause": "Lockdown closures", "quote": quote}]})
    frames = {"t": pd.DataFrame({"Year": [f"FY{i}" for i in range(9)], "Sales": range(9)})}
    text = "Revenue fell in FY20 as stores were closed during the 2020 lockdown."
    assert explain_breaks([pattern], frames, text, gateway_for(FakeLLM([reply]))) == int(kept)
    assert any("cause:" in e.detail for e in pattern.evidence) is kept


# ---------------------------------------------------------------- 7. the privacy cost is stated
def test_privacy_plan_states_what_statement_scaling_reveals():
    doc = SourceDocument(document_sha256="pat12345", tables=[PL], text_blocks=["x"])
    plan = plan_privacy(doc, infer(doc, CFG), CFG)
    assert any("statement table" in w and "scaled copies" in w for w in plan.warnings)
