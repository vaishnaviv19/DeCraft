# tests/core/test_hierarchy.py
from src.core.hierarchy import RowMeta, indent_levels, link_totals, verify_links
from src.core.rollup import pct_of_total, rollup


def test_indent_levels_from_x_positions():
    assert indent_levels([10.0, 10.4, 20.0, 20.2]) == [0, 0, 1, 1]


def test_total_row_links():
    rows = [RowMeta("North", 0, True, False), RowMeta("South", 0, True, False),
            RowMeta("Total", 0, True, True)]
    assert link_totals(rows) == {2: [0, 1]}


def test_nested_total_links_its_subtotal():
    rows = [RowMeta("A", 0, True, False), RowMeta("Sub", 0, True, True),
            RowMeta("C", 0, True, False), RowMeta("Total", 0, True, True)]
    assert link_totals(rows) == {1: [0], 3: [1, 2]}


def test_verify_links_match_rate_and_untestable():
    assert verify_links([1.0, 2.0, 3.0], {2: [0, 1]}) == (1.0, 1)
    assert verify_links([1.0, 2.0, 4.0], {2: [0, 1]}) == (0.0, 1)
    assert verify_links([1.0, None, 3.0], {2: [0, 1]}) == (None, 0)


def test_rollup_and_pct_of_total():
    assert rollup({2: [0, 1]}, {0: 1.0, 1: 2.0}) == {0: 1.0, 1: 2.0, 2: 3.0}
    assert pct_of_total([1.0, 3.0], 4.0) == [25.0, 75.0]
