# tests/inference/test_scoring.py
"""Inference scoring on the Fixture domains (M10.3): type accuracy, rule recall and zero falsely
enforced rules. Every fixture is a small source dataset with a hand-written expected inference."""
import json
from pathlib import Path

import pandas as pd
import pytest

from src.config import load_config
from src.contracts import SourceDocument, SourceTable
from src.inference import infer
from tests.conftest import FakeLLM

FIXTURES = sorted((Path(__file__).resolve().parents[1] / "fixtures").glob("*/"))


def load(fixture: Path):
    frame = pd.read_csv(fixture / "source.csv", dtype=str, keep_default_na=False)
    table = SourceTable(table_id="t", page=1, header=[str(c) for c in frame.columns],
                        raw_cells=[[str(v) for v in row]
                                   for row in frame.itertuples(index=False, name=None)])
    document = SourceDocument(document_sha256=fixture.name, tables=[table], text_blocks=[])
    expected = json.loads((fixture / "expected_inference.json").read_text(encoding="utf-8"))
    return document, expected


def infer_fixture(fixture: Path, gateway_for):
    document, expected = load(fixture)
    reply = expected.get("llm_reply")
    gateway = gateway_for(FakeLLM([json.dumps(reply)])) if reply else None
    report = infer(document, load_config(llm_enabled=reply is not None), gateway=gateway)
    return report, expected


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.name)
def test_types_rules_and_no_false_enforcement(fixture, gateway_for):
    report, expected = infer_fixture(fixture, gateway_for)
    profiles = {f"t.{p.column}": p for p in report.profiles}

    hits = sum(1 for key, want in expected["types"].items()
               if key in profiles and profiles[key].semantic_type == want)
    assert hits / len(expected["types"]) >= 0.8, [
        (key, profiles[key].semantic_type if key in profiles else None, want)
        for key, want in expected["types"].items()]
    for key, want in expected.get("physical", {}).items():
        assert profiles[key].physical_type == want, key

    enforced = {(r.kind.value, tuple(sorted(r.columns))): r for r in report.rules
                if r.enforcement.value == "hard"}
    expected_rules = {(r["kind"], tuple(sorted(r["columns"]))): r for r in expected["rules"]}
    assert set(expected_rules) <= set(enforced), (fixture.name, sorted(enforced))
    assert not (set(enforced) - set(expected_rules)), f"falsely enforced: {set(enforced) - set(expected_rules)}"
    for key, rule in expected_rules.items():
        assert enforced[key].tier.value == rule["tier"]

    assert report.llm_used is bool(expected.get("llm_reply"))


def test_every_fixture_has_a_source_and_an_expectation():
    assert len(FIXTURES) >= 3
    assert {"retail", "finance", "energy", "unknown"} <= {f.name for f in FIXTURES}
    for fixture in FIXTURES:
        assert (fixture / "source.csv").exists()
        assert (fixture / "expected_inference.json").exists()
