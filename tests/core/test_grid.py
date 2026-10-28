# tests/core/test_grid.py  -- E-51: relations found in the source, numbers regenerated consistently
import numpy as np

from src.contracts import SourceTable
from src.core.grid import (build_grid, derived_positions, detect, format_cell, generate_numbers,
                           number_style)


def _table(table_id, header, rows):
    return SourceTable(table_id=table_id, page=1, header=header, raw_cells=rows)


PL = _table("t_pl", ["Y.E March (Rs. cr)", "FY21A", "FY22A", "FY23A", "FY24E"], [
    ["Revenue", "32,703", "42,816", "55,109", "59,518"],
    ["% change", "12.2", "30.9", "28.7", "8.0"],
    ["EBITDA", "7,442", "8,431", "9,144", "10,192"],
    ["Depreciation", "2,745", "3,122", "3,439", "3,590"],
    ["EBIT", "4,697", "5,309", "5,705", "6,602"],
    ["EBITDA margin (%)", "22.8", "19.7", "16.6", "17.1"],
])
SUMMARY = _table("t_sum", ["Consolidated (cr)", "FY22A", "FY23A", "FY24E"], [
    ["Sales", "42,816", "55,109", "59,518"],
    ["EBITDA", "8,431", "9,144", "10,192"],
])


def _relations():
    grids = {t.table_id: build_grid(t) for t in (PL, SUMMARY)}
    return grids, detect(grids)


def test_statement_relations_are_found_in_the_source():
    _, rel = _relations()
    kinds = {(key[0], key[1]): r.kind for key, r in rel.rel.items()}
    assert kinds[("t_pl", 4)] == "sum"                      # EBIT = EBITDA - Depreciation
    assert kinds[("t_pl", 1)] == "growth"                   # % change of Revenue
    assert kinds[("t_pl", 5)] == "ratio"                    # margin = EBITDA / Revenue
    ebit = rel.rel[("t_pl", 4, 1)]
    assert {(s, x[1]) for s, x in ebit.terms} == {(1.0, 2), (-1.0, 3)}


def test_the_same_figure_in_two_tables_is_one_fact():
    _, rel = _relations()
    assert rel.find(("t_sum", 0, 2)) == rel.find(("t_pl", 0, 3))      # Sales FY23A = Revenue FY23A


def test_generated_numbers_keep_every_relation_and_move_away_from_the_source():
    grids, rel = _relations()
    values = generate_numbers(rel, np.random.default_rng(7), 0.05)
    for c in range(1, 5):
        assert values[("t_pl", 4, c)] == round(values[("t_pl", 2, c)] - values[("t_pl", 3, c)], 0)
        margin = 100 * values[("t_pl", 2, c)] / values[("t_pl", 0, c)]
        assert abs(values[("t_pl", 5, c)] - margin) <= 0.05 + 1e-9
    for c in range(2, 5):
        growth = 100 * (values[("t_pl", 0, c)] / values[("t_pl", 0, c - 1)] - 1)
        assert abs(values[("t_pl", 1, c)] - growth) <= 0.05 + 1e-9
    assert values[("t_sum", 0, 2)] == values[("t_pl", 0, 3)]
    for key, (old, _) in rel.value.items():                  # base cells moved >= 5%
        if key not in rel.rel and rel.find(key) not in {rel.find(k) for k in rel.rel}:
            assert abs(values[key] - old) / abs(old) >= 0.05


def test_a_total_that_is_the_same_in_every_column_stays_and_its_parts_add_up():
    table = _table("t_share", ["Shareholding %", "Q1", "Q2"], [
        ["Promoters", "46.9", "46.9"], ["Public", "28.0", "28.5"], ["Others", "25.1", "24.6"],
        ["Total", "100.0", "100.0"]])
    grids = {"t_share": build_grid(table)}
    rel = detect(grids)
    assert rel.rel[("t_share", 3, 1)].fixed
    values = generate_numbers(rel, np.random.default_rng(3), 0.05)
    for c in (1, 2):
        assert values[("t_share", 3, c)] == 100.0
        assert round(sum(values[("t_share", r, c)] for r in range(3)), 1) == 100.0


def test_change_columns_and_bps_rows_are_recomputed():
    table = _table("t_q", ["", "Q1FY24", "Q1FY23", "YoY Growth %"], [
        ["Revenue", "15,213", "14,495", "5.0"], ["EBITDA", "3,215", "1,827", "76.0"],
        ["EBIT", "2,322", "1,005", "131.1"], ["EBITDA margins", "21.1", "12.6", "850bps"]])
    rel = detect({"t_q": build_grid(table)})
    assert rel.rel[("t_q", 0, 3)].kind == "growth"
    assert rel.rel[("t_q", 3, 3)].kind == "bps"


def test_low_precision_rows_are_never_explained_by_chance():
    table = _table("t_r", ["Y.E March", "FY21A", "FY22A", "FY23A"], [
        ["Current ratio (x)", "0.8", "0.8", "0.9"], ["Quick ratio (x)", "0.4", "0.3", "0.4"],
        ["Asset T.O (x)", "0.3", "0.4", "0.5"]])
    rel = detect({"t_r": build_grid(table)})
    assert not rel.rel


def test_cells_print_in_their_source_format():
    style = {"commas": True, "brackets": True}
    assert format_cell("884", 1141.0, style) == "1,141"
    assert format_cell("12.2", -3.4, style) == "(3.4)"
    assert format_cell("Rs. 86,856", 91234.4, style) == "Rs. 91,234"
    assert format_cell("850bps", -120, style) == "(120bps)"
    assert format_cell("53.0%", 61.24, style) == "61.2%"
    assert number_style(build_grid(PL)) == {"brackets": False, "commas": True, "indian": False}


def test_derived_positions_name_the_recomputed_cells():
    class Doc:
        document_sha256 = "test-derived"
        tables = [PL, SUMMARY]
        text_blocks = []
    cells = derived_positions(Doc())
    assert (4, 1) in cells["t_pl"] and (0, 1) not in cells["t_pl"]


def test_balance_sheet_totals_one_unit_apart_stay_equal():
    """Total liabilities and total assets printed 1 apart (rounding) are one figure in the decoy."""
    table = _table("t_bs", ["(Rs mn)", "FY-22", "FY-23", "FY-24E", "FY-25E"], [
        ["Equity", "2,24,416", "2,87,874", "3,26,093", "3,69,502"],
        ["Debt", "8,70,615", "9,93,085", "10,49,348", "11,10,500"],
        ["Total", "10,95,031", "12,80,959", "13,75,441", "14,80,002"],
        ["Fixed Assets", "6,20,245", "6,71,220", "7,18,205", "7,68,480"],
        ["Current Assets", "4,74,787", "6,09,738", "6,57,236", "7,11,522"],
        ["Total", "10,95,032", "12,80,958", "13,75,441", "14,80,002"],
    ])
    grids = {"t_bs": build_grid(table)}
    rel = detect(grids)
    values = generate_numbers(rel, np.random.default_rng(5), 0.05)
    for c in range(1, 5):
        assert values[("t_bs", 2, c)] == values[("t_bs", 5, c)]
        assert values[("t_bs", 2, c)] == values[("t_bs", 0, c)] + values[("t_bs", 1, c)]
        assert values[("t_bs", 5, c)] == values[("t_bs", 3, c)] + values[("t_bs", 4, c)]
