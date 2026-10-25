# tests/inference/test_quality.py
from src.contracts import ExtractionGrade, SourceTable
from src.inference._quality import downgrade_for_total_mismatch, grade_table


def table(header, rows, ocr=False):
    return SourceTable(table_id="t1", page=1, header=header, raw_cells=rows, ocr=ocr)


def test_clean_table_is_good():
    q = grade_table(table(["Region", "Revenue"], [["North", "1,200"], ["South", "980"]]))
    assert q.grade is ExtractionGrade.GOOD and q.issues == []


def test_ragged_rows_are_bad():
    q = grade_table(table(["Region", "Revenue"], [["North", "1,200"], ["South"], ["East", "1", "x"]]))
    assert q.grade is ExtractionGrade.BAD
    assert any("length" in i for i in q.issues)


def test_duplicate_header_is_doubtful():
    q = grade_table(table(["Region", "Region"], [["North", "1"], ["South", "2"]]))
    assert q.grade is ExtractionGrade.DOUBTFUL
    assert any("duplicated" in i for i in q.issues)


def test_ocr_is_doubtful():
    assert grade_table(table(["A", "B"], [["1", "2"]], ocr=True)).grade is ExtractionGrade.DOUBTFUL


def test_parse_failures_above_20_percent_are_doubtful():
    rows = [["1", "ok"], ["2", "ok"], ["x", "ok"], ["4", "ok"], ["5", "ok"], ["y", "ok"]]
    q = grade_table(table(["Value", "Label"], rows))
    assert q.grade is ExtractionGrade.DOUBTFUL
    assert any("numeric parse failures" in i for i in q.issues)


def test_parse_failures_above_50_percent_are_bad():
    rows = [["1", "ok"], ["x", "ok"], ["y", "ok"], ["z", "ok"]]
    assert grade_table(table(["Value", "Label"], rows)).grade is ExtractionGrade.BAD


def test_plain_text_column_with_a_few_numbers_is_not_condemned():
    rows = [["Revenue grew 12%", "ok"], ["Costs fell", "ok"], ["Margins 2023", "ok"], ["Hiring", "ok"]]
    assert grade_table(table(["Comment", "Label"], rows)).grade is ExtractionGrade.GOOD


def test_single_column_table_is_not_flagged_as_merged_cells():
    """B-09: the merged-cell heuristic needs at least two columns."""
    q = grade_table(table(["Item"], [["Revenue"], ["Cost"]]))
    assert not any("merged cells" in i for i in q.issues)


def test_label_only_rows_are_doubtful():
    q = grade_table(table(["Item", "Value"], [["Revenue", None], ["Cost", None]]))
    assert q.grade is ExtractionGrade.DOUBTFUL
    assert any("merged cells" in i for i in q.issues)


def test_total_mismatch_downgrades_good_to_doubtful():
    q = grade_table(table(["Region", "Revenue"], [["North", "1"], ["South", "2"]]))
    down = downgrade_for_total_mismatch(q, total_match_rate=0.5, strong=0.95)
    assert down.grade is ExtractionGrade.DOUBTFUL
    assert "total rows do not reconcile" in down.issues
    assert downgrade_for_total_mismatch(q, 0.99, 0.95).grade is ExtractionGrade.GOOD
