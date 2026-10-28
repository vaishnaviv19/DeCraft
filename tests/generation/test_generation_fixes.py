# tests/generation/test_generation_fixes.py
"""Generation keeps document structure (line items, summary labels, blanks), prints numbers that add up,
keeps ID formats, follows each column's privacy method, and keeps correlations between columns."""
import json

import numpy as np
import pandas as pd

from src.config import load_config
from src.contracts import (Cardinality, ColumnRef, DecisionStatus, Relationship, RuleTier, SourceDocument,
                           SourceTable)
from src.core import expr
from src.generation import generate
from src.generation._apply import GenerationContext, apply_steps, correlated_samples
from src.generation._graph import Step
from src.inference import _formula, infer
from src.privacy import plan_privacy
from tests.conftest import FakeLLM

CFG = load_config(llm_enabled=False)


def doc(*tables):
    return SourceDocument(document_sha256="gen12345", tables=list(tables), text_blocks=["text"])


def run(document, cfg=CFG, gateway=None, seed=1):
    report = infer(document, cfg, gateway=gateway)
    plan = plan_privacy(document, report, cfg, gateway=gateway)
    return report, plan, generate(document, report, plan, cfg, np.random.default_rng(seed))


def annotation(columns, proposals=()):
    return json.dumps({"domain": "finance", "domain_confidence": 0.8, "proposals": list(proposals),
                       "columns": [{"column": c, "meaning": c, "role": role, "importance": "medium",
                                    "sensitivity": sens, "sensitivity_reason": "because", "confidence": 0.9}
                                   for c, role, sens in columns]})


PL = SourceTable(table_id="pl", page=1, header=["Line item", "FY24", "FY25"], raw_cells=[
    ["Revenue", "1,200", "1,350"], ["Cost of sales", "700", "760"], ["Gross profit", "500", "590"],
    ["Operating expenses", "300", "320"], ["Operating profit", "200", "270"], ["Tax", "50", "65"],
    ["Net profit", "150", "205"]])
LABELS = ["Revenue", "Cost of sales", "Gross profit", "Operating expenses", "Operating profit", "Tax",
          "Net profit"]


# ---------------------------------------------------------------- 1. structure
def test_statement_line_items_keep_their_order():
    _, _, data = run(doc(PL))
    assert list(data.tables["pl"]["Line item"]) == LABELS


def test_llm_row_label_role_keeps_repeated_labels_in_order(gateway_for):
    seg = SourceTable(table_id="seg", page=1, header=["Metric", "Segment", "Value"], raw_cells=[
        [m, s, str(v)] for (m, s), v in zip([("Revenue", "A"), ("Margin", "A"), ("Revenue", "B"),
                                             ("Margin", "B"), ("Revenue", "C"), ("Margin", "C")],
                                            [100, 20, 140, 25, 90, 18])])
    cfg = CFG.model_copy(update={"llm_enabled": True})
    reply = annotation([("Metric", "row_label", "low_risk"), ("Segment", "category", "low_risk"),
                        ("Value", "currency", "business_metric")])
    _, _, data = run(doc(seg), cfg, gateway_for(FakeLLM([reply])))
    assert list(data.tables["seg"]["Metric"]) == ["Revenue", "Margin"] * 3


def test_summary_labels_are_not_repeated_or_dropped():
    detail = SourceTable(table_id="sales", page=1, header=["Region", "Amount"], raw_cells=[
        [r, str(a)] for r, a in zip(["North", "North", "South", "South", "East", "East", "West", "West"],
                                    [10, 20, 30, 40, 50, 60, 70, 80])])
    summary = SourceTable(table_id="by_region", page=2, header=["Region", "Total"], raw_cells=[
        ["North", "30"], ["South", "70"], ["East", "110"], ["West", "150"]])
    _, _, data = run(doc(detail, summary))
    assert list(data.tables["by_region"]["Region"]) == ["North", "South", "East", "West"]


def test_source_blanks_stay_blank_and_validation_passes():
    t = SourceTable(table_id="m", page=1, header=["Item", "Value"], raw_cells=[
        ["a", "1"], ["b", ""], ["c", "n/a"], ["d", "4"], ["e", "5"], ["f", "-"], ["g", "7"], ["h", "8"]])
    _, _, data = run(doc(t))
    assert data.tables["m"]["Value"].isna().tolist() == [False, True, True, False, False, True, False, False]
    assert data.validation.ok


# ---------------------------------------------------------------- 2. printed numbers add up
def test_printed_inputs_give_the_printed_formula_output(gateway_for):
    rows = [[f"A-{101 + i}", str(u), f"{p:.1f}", f"{u * p:.1f}"]
            for i, (u, p) in enumerate([(12, 4.5), (8, 5.0), (15, 4.2), (9, 4.8), (11, 4.6), (7, 5.2)])]
    sales = SourceTable(table_id="s", page=1, header=["Ref", "Units", "Unit Price", "Revenue"], raw_cells=rows)
    cfg = CFG.model_copy(update={"llm_enabled": True})
    reply = annotation([], [{"kind": "formula", "columns": ["Revenue", "Units", "Unit Price"],
                             "params": {"expression": "[Revenue] = [Units] * [Unit Price]"}}])
    for seed in range(5):
        _, _, data = run(doc(sales), cfg, gateway_for(FakeLLM([reply])), seed=seed)
        shown = data.display["s"]
        assert np.allclose(shown["Units"] * shown["Unit Price"], shown["Revenue"], atol=0.051)


# ---------------------------------------------------------------- 3. the privacy plan is followed
def test_trend_preserving_metrics_keep_their_shape(gateway_for):
    years = SourceTable(table_id="y", page=1, header=["Year", "Revenue"], raw_cells=[
        [f"Y{i}", str(v)] for i, v in enumerate([100, 120, 90, 150, 170, 160, 200, 210])])
    cfg = CFG.model_copy(update={"llm_enabled": True})
    reply = annotation([("Year", "row_label", "low_risk"), ("Revenue", "currency", "business_metric")])
    report, plan, data = run(doc(years), cfg, gateway_for(FakeLLM([reply])))
    assert next(d for d in plan.decisions if d.column == "Revenue").method == "trend_preserving"
    source = pd.Series([100, 120, 90, 150, 170, 160, 200, 210], dtype=float)
    generated = data.tables["y"]["Revenue"]
    assert generated.rank().tolist() == source.rank().tolist()       # ups and downs are the same
    assert ((generated - source).abs() / source >= 0.05).all()


def test_preserved_entity_columns_are_copied_unchanged():
    t = SourceTable(table_id="e", page=1, header=["Company", "Units"], raw_cells=[
        ["Acme", str(v)] for v in (5, 7, 9, 11, 13, 15)])
    document = SourceDocument(document_sha256="gen12345", entity_name="Acme", tables=[t], text_blocks=["x"])
    _, _, data = run(document)
    assert set(data.tables["e"]["Company"]) == {"Acme"}


def test_correlated_columns_stay_correlated():
    rng = np.random.default_rng(0)
    units = rng.uniform(50, 150, 60)
    columns = {"Units": list(units), "Cost": list(units * 30 + rng.normal(0, 100, 60))}
    joint = correlated_samples(columns, 60, np.random.default_rng(1), 0.05, {})
    rho = pd.Series(joint["Units"]).corr(pd.Series(joint["Cost"]), method="spearman")
    assert rho > 0.8


def test_too_few_rows_for_a_correlation_falls_back_to_independent_sampling():
    joint = correlated_samples({"a": [1.0, 2.0, 3.0], "b": [3.0, 2.0, 1.0]}, 3, np.random.default_rng(0), 0.05, {})
    assert set(joint) == {"a", "b"} and all(len(v) == 3 for v in joint.values())


# ---------------------------------------------------------------- 4. links and keys
def _fk_ctx(child_values):
    parent = pd.DataFrame({"id": ["P-1", "P-2"]})
    ctx = GenerationContext(source={"p": parent, "c": pd.DataFrame({"pid": child_values})},
                            profiles={"p": [], "c": []}, decimals={}, total_links={},
                            tables={"p": parent.copy(), "c": pd.DataFrame(index=range(len(child_values)))})
    rel = Relationship(rel_id="r", parent=ColumnRef(table_id="p", column="id"),
                       child=ColumnRef(table_id="c", column="pid"), cardinality=Cardinality.ONE_TO_ONE,
                       overlap_rate=1.0, parent_unique_rate=1.0, tier=RuleTier.STRONGLY_INFERRED,
                       status=DecisionStatus.ACCEPTED)
    apply_steps(ctx, [Step("copy_fk", "c.pid", "r")], [], [rel], set(), CFG.thresholds, np.random.default_rng(0))
    return ctx


def test_one_to_one_link_with_more_rows_than_parents_does_not_crash():
    ctx = _fk_ctx(["P-1", "P-2", "P-1"])
    assert set(ctx.tables["c"]["pid"]) <= {"P-1", "P-2"}
    assert any("one-to-one not possible" in w for w in ctx.warnings)


def test_blank_links_stay_blank():
    ctx = _fk_ctx(["P-1", None])
    assert ctx.tables["c"]["pid"].tolist()[1] is None and ctx.warnings == []


def test_keys_without_digits_are_relabelled_by_the_plan():
    t = SourceTable(table_id="k", page=1, header=["Store", "Sales"], raw_cells=[
        ["Alpha", "10"], ["Beta", "20"], ["Gamma", "30"], ["Delta", "40"], ["Omega", "50"]])
    document = doc(t)
    report = infer(document, CFG)
    report.key_columns["k"] = ["Store"]
    plan = plan_privacy(document, report, CFG)
    assert next(d for d in plan.decisions if d.column == "Store").relabel


# ---------------------------------------------------------------- 5. one evaluator
def test_inference_and_generation_share_one_formula_evaluator():
    assert _formula.evaluate is expr.evaluate
