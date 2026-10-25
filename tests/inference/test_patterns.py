# tests/inference/test_patterns.py
from src.contracts import Direction, PatternKind, SourceDocument, SourceTable
from src.inference import infer


def document(table: SourceTable) -> SourceDocument:
    return SourceDocument(document_sha256="abc", tables=[table], text_blocks=["text"])


def period_table(header, rows) -> SourceTable:
    return SourceTable(table_id="t", page=1, header=header, raw_cells=rows)


def years(count: int = 10) -> list[str]:
    return [f"FY{16 + i}A" for i in range(count)]


def test_a_rising_series_emits_an_up_trend(cfg):
    rows = [[f"FY{16 + i}A", str(100 + 10 * i)] for i in range(10)]
    report = infer(document(period_table(["Period", "Revenue"], rows)), cfg)
    trends = [p for p in report.patterns if p.kind is PatternKind.TREND]
    assert trends and trends[0].direction is Direction.UP
    assert trends[0].tier == "confirmed" and trends[0].on_residuals is False
    assert trends[0].statistic_name == "kendall_tau"


def test_a_table_without_a_time_axis_emits_no_patterns(cfg):
    rows = [["North", str(10 + i)] for i in range(10)]
    report = infer(document(period_table(["Region", "Revenue"], rows)), cfg)
    assert report.patterns == []


def test_co_movement_survives_detrending(cfg):
    wiggles = [0, 3, -2, 4, -3, 2, 5, -4, 3, 1]
    rows = [[period, str(100 + 7 * i + w), str(60 + 4 * i + w * 0.5)]
            for i, (period, w) in enumerate(zip(years(), wiggles))]
    report = infer(document(period_table(["Period", "Revenue", "Cost"], rows)), cfg)
    correlations = [p for p in report.patterns if p.kind is PatternKind.CORRELATION]
    assert correlations and correlations[0].direction is Direction.POSITIVE
    assert correlations[0].on_residuals is True and len(correlations[0].fields) == 2


def test_crash_series_emits_a_segment_shape(cfg):
    crash = [1, 2, 3, 4, 5, 6, 7, 8, 9, 8, 7, 6, 5, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    rows = [[f"FY{2000 + i}", str(v)] for i, v in enumerate(crash)]
    report = infer(document(period_table(["Period", "Revenue"], rows)), cfg)
    shapes = [p for p in report.patterns if p.kind is PatternKind.SEGMENTS]
    assert shapes and [s.direction for s in shapes[0].segments] == ["up", "down", "up"]
