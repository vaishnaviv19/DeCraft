# tests/inference/test_row_formulas.py
"""Row formulas: the LLM reads what statement line items MEAN and proposes arithmetic between rows
(Gross profit = Revenue - Cost of sales); Python checks it in every number column; generation keeps it."""
import json

import numpy as np
import pandas as pd
import pytest

from src.contracts import DecisionStatus, Enforcement, RuleKind, SourceDocument, SourceTable
from src.generation import generate
from src.inference import infer, statement_labels
from src.inference._annotator import TablePayload, build_user_prompt
from src.inference._verifiers import verify_row_formula
from src.privacy import plan_privacy
from tests.conftest import FakeLLM

PL = SourceTable(table_id="pl", page=1, header=["Line item", "FY24", "FY25"], raw_cells=[
    ["Revenue", "1,200", "1,350"], ["Cost of sales", "700", "760"], ["Gross profit", "500", "590"],
    ["Operating expenses", "300", "320"], ["Operating profit", "200", "270"], ["Tax", "50", "65"],
    ["Net profit", "150", "205"]])
TRUE_RULES = ["[Gross profit] = [Revenue] - [Cost of sales]",
              "[operating profit] = [GROSS PROFIT] - [Operating  expenses]",     # case/spacing do not matter
              "[Net profit] = [Operating profit] - [Tax]"]


def row_rule(expression):
    return {"kind": "row_formula", "columns": ["Line item"],
            "params": {"label_column": "Line item", "expression": expression}}


def reply(*expressions):
    return json.dumps({"domain": "finance", "domain_confidence": 0.9, "columns": [],
                       "proposals": [row_rule(e) for e in expressions]})


def run(cfg, gateway_for, expressions, seed=1):
    llm_cfg = cfg.model_copy(update={"llm_enabled": True})
    llm = FakeLLM([reply(*expressions), json.dumps({}), json.dumps({"columns": []})])
    gateway = gateway_for(llm)
    document = SourceDocument(document_sha256="rows1234", tables=[PL], text_blocks=["Annual report"])
    report = infer(document, llm_cfg, gateway=gateway)
    plan = plan_privacy(document, report, llm_cfg, gateway=gateway)
    return llm, report, generate(document, report, plan, llm_cfg, np.random.default_rng(seed))


def row_rules(report):
    return [r for r in report.rules if r.kind is RuleKind.ROW_FORMULA]


# ---------------------------------------------------------------- what the LLM sees
def test_the_llm_sees_every_line_item_of_a_statement(cfg, gateway_for):
    llm, _, _ = run(cfg, gateway_for, [])
    data = json.loads(llm.calls[0][1].split("<data>")[1].split("</data>")[0])
    assert data["label_column"] == "Line item"
    assert data["row_labels"] == ["Revenue", "Cost of sales", "Gross profit", "Operating expenses",
                                  "Operating profit", "Tax", "Net profit"]       # not only 3 sample rows


def test_a_customer_list_does_not_send_all_its_names():
    df = pd.DataFrame({"Customer": [f"Person {i}" for i in range(10)], "Spend": range(10)})
    from src.contracts import FieldProfile
    profiles = [FieldProfile(table_id="t", column="Customer", physical_type="text", n=10, n_missing=0, n_unique=10),
                FieldProfile(table_id="t", column="Spend", physical_type="integer", n=10, n_missing=0, n_unique=10)]
    assert statement_labels(df, profiles) == (None, [])


def test_prompt_explains_row_formulas():
    user = build_user_prompt(TablePayload(name="pl", columns=["Line item", "FY24"], sample_rows=[],
                                          label_column="Line item", row_labels=["Revenue"]))
    assert '"row_labels": ["Revenue"]' in user


# ---------------------------------------------------------------- Python checks every proposal
def test_true_statement_relations_become_hard_rules(cfg, gateway_for):
    _, report, _ = run(cfg, gateway_for, TRUE_RULES)
    rules = row_rules(report)
    assert len(rules) == 3
    assert all(r.enforcement is Enforcement.HARD and r.match_rate == 1.0 and r.n_rows == 2 for r in rules)
    assert rules[1].formula.text == "[Operating profit] = [Gross profit] - [Operating expenses]"


@pytest.mark.parametrize("expression,why", [
    ("[Tax] = [Revenue] - [Net profit]", "false in the data"),
    ("[Gross profit] = [Revenue] - [Marketing]", "unknown row"),
    ("[Gross profit] = __import__('os')", "unsafe"),
])
def test_wrong_or_unsafe_row_formulas_are_never_enforced(cfg, gateway_for, expression, why):
    _, report, _ = run(cfg, gateway_for, [expression])
    (rule,) = row_rules(report)
    assert rule.enforcement is Enforcement.NONE and rule.status is DecisionStatus.REJECTED, why


def test_row_formula_verifier_counts_each_number_column():
    df = pd.DataFrame({"Item": ["A", "B", "C"], "Y1": [10.0, 4.0, 6.0], "Y2": [20.0, 5.0, 15.0],
                       "Y3": [30.0, 10.0, 99.0]})
    from src.config import load_config as lc
    r = verify_row_formula(df, "Item", "[C] = [A] - [B]", {}, lc(llm_enabled=False).thresholds)
    assert (r.n_rows, r.match_rate) == (3, pytest.approx(2 / 3))


# ---------------------------------------------------------------- generation keeps them
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_generated_statement_adds_up(cfg, gateway_for, seed):
    _, _, data = run(cfg, gateway_for, TRUE_RULES, seed)
    shown = data.display["pl"].set_index("Line item")
    for year in ("FY24", "FY25"):
        col = shown[year]
        assert col["Gross profit"] == pytest.approx(col["Revenue"] - col["Cost of sales"])
        assert col["Operating profit"] == pytest.approx(col["Gross profit"] - col["Operating expenses"])
        assert col["Net profit"] == pytest.approx(col["Operating profit"] - col["Tax"])
    assert data.validation.ok
    assert data.lineage["pl.row:Net profit"].startswith("row_rule:")
