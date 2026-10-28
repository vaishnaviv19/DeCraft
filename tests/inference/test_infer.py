# tests/inference/test_infer.py
import json
from datetime import date

from src.contracts import (Enforcement, ExtractionGrade, InferenceReport, RuleKind, RuleTier,
                           SourceDocument, SourceTable)
from src.inference import infer
from tests.conftest import FakeLLM


def doc_with(*tables, sha="abc123", text_blocks=("text",)):
    return SourceDocument(document_sha256=sha, entity_name="Acme", tables=list(tables),
                          text_blocks=list(text_blocks))


GOOD = SourceTable(table_id="t_good", page=1, header=["Region", "Revenue"],
                   raw_cells=[["North", "$1,000.00"], ["South", "$2,000.00"]])
BAD = SourceTable(table_id="t_bad", page=2, header=["Region", "Revenue"],
                  raw_cells=[["North", "$1,000.00"], ["South"], ["East", "1", "x"]])


def sales_table(sales_df, **kw):
    rows = [[str(v) for v in row] for row in sales_df.itertuples(index=False)]
    return SourceTable(table_id="t_sales", page=1, header=list(sales_df.columns), raw_cells=rows, **kw)


def llm_cfg(cfg):
    return cfg.model_copy(update={"llm_enabled": True})


def formula_reply(expression="[Revenue] = [Units] * [Unit Price]"):
    return json.dumps({"domain": "retail", "domain_confidence": 0.8, "columns": [],
                       "proposals": [{"kind": "formula", "columns": ["Revenue", "Units", "Unit Price"],
                                      "params": {"expression": expression}}]})


def test_one_table_quality_per_table(cfg):
    report = infer(doc_with(GOOD, BAD), cfg)
    assert isinstance(report, InferenceReport)
    assert set(report.table_quality) == {"t_good", "t_bad"}
    assert report.table_quality["t_good"].grade is ExtractionGrade.GOOD
    assert report.table_quality["t_bad"].grade is ExtractionGrade.BAD


def test_bad_table_has_no_profiles_and_is_logged(cfg):
    report = infer(doc_with(GOOD, BAD), cfg)
    assert {p.table_id for p in report.profiles} == {"t_good"}
    assert any("t_bad" in w and "skipped" in w for w in report.warnings)


def test_report_carries_hashes_and_llm_used_is_false(cfg, gateway_for):
    report = infer(doc_with(GOOD), cfg, gateway=gateway_for(None))
    assert report.source_sha256 == "abc123"
    assert report.config_hash == cfg.config_hash()
    assert report.llm_used is False


def test_report_round_trips_json(cfg):
    report = infer(doc_with(GOOD, BAD), cfg)
    assert InferenceReport.model_validate_json(report.model_dump_json()) == report


def test_profiles_are_marked_with_the_table_grade(cfg):
    doubtful = SourceTable(table_id="t_d", page=1, header=["A", "B"],
                           raw_cells=[["1", "2"]], ocr=True)
    report = infer(doc_with(doubtful), cfg)
    assert report.table_quality["t_d"].grade is ExtractionGrade.DOUBTFUL
    assert all(p.extraction_grade is ExtractionGrade.DOUBTFUL for p in report.profiles)


def test_llm_formula_becomes_a_hard_strongly_inferred_rule(cfg, gateway_for, sales_df):
    report = infer(doc_with(sales_table(sales_df)), llm_cfg(cfg),
                   gateway=gateway_for(FakeLLM([formula_reply()])))
    rule = next(r for r in report.rules if r.kind is RuleKind.FORMULA)
    assert (rule.tier, rule.status.value, rule.enforcement) == (
        RuleTier.STRONGLY_INFERRED, "accepted", Enforcement.HARD)
    assert rule.formula is not None and set(rule.formula.inputs) == {"Units", "Unit Price"}
    assert report.llm_used is True and report.domain == "retail"
    assert any(e.source == "llm_proposal" for e in rule.evidence)


def test_doubtful_table_caps_the_same_proposal_at_hypothesized(cfg, gateway_for, sales_df):
    report = infer(doc_with(sales_table(sales_df, ocr=True)), llm_cfg(cfg),
                   gateway=gateway_for(FakeLLM([formula_reply()])))
    rule = next(r for r in report.rules if r.kind is RuleKind.FORMULA)
    assert (rule.tier, rule.enforcement) == (RuleTier.HYPOTHESIZED, Enforcement.NONE)


def test_llm_off_finds_only_total_columns(cfg):
    rows = [[f"R{i}", str(i + 1), str(i + 2), str(2 * i + 3)] for i in range(6)]
    table = SourceTable(table_id="t_tot", page=1, header=["Item", "Q1", "Q2", "Total"],
                        raw_cells=rows)
    report = infer(doc_with(table), cfg, gateway=None)
    assert report.llm_used is False
    assert [r.kind for r in report.rules] == [RuleKind.FORMULA]
    rule = report.rules[0]
    assert rule.enforcement is Enforcement.HARD and rule.n_rows == 6
    assert rule.columns == ["Total", "Q1", "Q2"]
    assert any(e.source == "rule_detector" for e in rule.evidence)


def test_llm_disabled_in_config_never_calls_the_gateway(cfg, gateway_for, sales_df):
    llm = FakeLLM([formula_reply()])
    infer(doc_with(sales_table(sales_df)), cfg, gateway=gateway_for(llm))
    assert llm.calls == []


def test_reference_date_comes_from_the_document_text(cfg):
    report = infer(doc_with(GOOD, text_blocks=["As at 31 March 2024 the group reported"]), cfg)
    assert report.reference_date == date(2024, 3, 31)


def test_total_row_marks_the_column_additive(cfg):
    table = SourceTable(table_id="t_links", page=1, header=["Item", "Value"],
                        raw_cells=[["A", "1"], ["B", "2"], ["Total", "3"]])
    report = infer(doc_with(table), cfg)
    value = next(p for p in report.profiles if p.column == "Value")
    assert value.additive is True


def test_forecast_periods_do_not_set_the_reference_date(cfg):
    table = SourceTable(table_id="t_p", page=1, header=["Item", "Period"],
                        raw_cells=[["A", "Q1FY24"], ["B", "FY25E"]])
    assert infer(doc_with(table), cfg).reference_date == date(2024, 3, 31)
