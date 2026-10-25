# tests/validation/test_patterns.py
import pandas as pd

from src.config import load_config
from src.contracts import (ColumnRef, Direction, PatternKind, PatternSpec, Severity, SourceDocument,
                           SourceTable, ValidationReport)
from src.contracts.dataset import CanonicalDataset
from src.inference import infer
from src.validation import validate


def dataset(values) -> CanonicalDataset:
    frame = pd.DataFrame({"v": values})
    return CanonicalDataset(dataset_id="d", schema_version="2.3", seed=42, tables={"t": frame},
                            display={}, key_columns={}, rules=[], facts={}, charts={}, lineage={},
                            validation=ValidationReport(ok=True))


def trend_pattern(direction: Direction) -> PatternSpec:
    return PatternSpec(pattern_id="t.v.trend", kind=PatternKind.TREND,
                       fields=[ColumnRef(table_id="t", column="v")],
                       direction=direction, n=6, tier="confirmed", uncertain=False)


def test_a_flipped_confirmed_trend_is_an_error(cfg):
    doc = SourceDocument(document_sha256="abc", text_blocks=["text"],
                         tables=[SourceTable(table_id="t", page=1, header=["v"],
                                             raw_cells=[[str(i)] for i in range(1, 7)])])
    report = infer(doc, cfg)
    report.patterns = [trend_pattern(Direction.UP)]
    found = validate(dataset([6.0, 5.0, 4.0, 3.0, 2.0, 1.0]), report, cfg)
    assert any(v.check == "utility_direction" and v.severity is Severity.ERROR
               for v in found.violations) and not found.ok


def test_a_kept_trend_passes(cfg):
    doc = SourceDocument(document_sha256="abc", text_blocks=["text"],
                         tables=[SourceTable(table_id="t", page=1, header=["v"],
                                             raw_cells=[[str(i)] for i in range(1, 7)])])
    report = infer(doc, cfg)
    report.patterns = [trend_pattern(Direction.UP)]
    found = validate(dataset([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]), report, cfg)
    assert not any(v.check == "utility_direction" for v in found.violations)
