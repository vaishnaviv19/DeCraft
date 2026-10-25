# tests/generation/test_generate.py
import json

import numpy as np
import pytest

from src.config import load_config
from src.contracts import Enforcement, PatternKind, SourceDocument, SourceTable
from src.core.stats import classify_trend
from src.generation import generate
from src.inference import infer
from src.privacy import plan_privacy
from tests.conftest import FakeLLM

SALES = [(120, 45, 3900), (80, 50, 2800), (150, 42, 4700),
         (95, 48, 3300), (110, 46, 3700), (70, 52, 2600)]
REGIONS = ["North", "South", "North", "East", "South", "West"]


def sales_document() -> SourceDocument:
    rows = [[f"A-{101 + i}", REGIONS[i], str(u), str(p), str(u * p), str(c), str(u * p - c)]
            for i, (u, p, c) in enumerate(SALES)]
    table = SourceTable(table_id="t_sales", page=1,
                        header=["Ref", "Region", "Units", "Unit Price", "Revenue", "Cost", "Profit"],
                        raw_cells=rows)
    return SourceDocument(document_sha256="abc12345", tables=[table], text_blocks=["text"])


def formula_reply() -> str:
    return json.dumps({"domain": "retail", "domain_confidence": 0.8, "columns": [],
                       "proposals": [{"kind": "formula",
                                      "columns": ["Revenue", "Units", "Unit Price"],
                                      "params": {"expression": "[Revenue] = [Units] * [Unit Price]"}}]})


def doc_with(*tables) -> SourceDocument:
    return SourceDocument(document_sha256="abc12345", tables=list(tables), text_blocks=["text"])


def run(doc, cfg=None, gateway=None):
    cfg = cfg or load_config(llm_enabled=False)
    report = infer(doc, cfg, gateway=gateway)
    plan = plan_privacy(doc, report, cfg)
    return report, generate(doc, report, plan, cfg, np.random.default_rng(cfg.seed))


def test_formula_holds_on_every_row_and_keys_are_fresh(cfg, gateway_for):
    doc = sales_document()
    llm_cfg = cfg.model_copy(update={"llm_enabled": True})
    report = infer(doc, llm_cfg, gateway=gateway_for(FakeLLM([formula_reply()])))
    data = generate(doc, report, plan_privacy(doc, report, llm_cfg), llm_cfg,
                    np.random.default_rng(1))
    frame = data.tables["t_sales"]
    assert len(frame) == len(SALES)
    assert np.allclose(frame["Revenue"], frame["Units"] * frame["Unit Price"], rtol=1e-9)
    assert set(frame["Ref"]).isdisjoint({f"A-{101 + i}" for i in range(len(SALES))})
    assert data.key_columns == {"t_sales": ["Ref"]}
    assert any(rule.enforcement is Enforcement.HARD for rule in data.rules)
    assert data.lineage["t_sales.Revenue"].startswith("rule:")


def test_total_row_equals_the_sum_of_its_leaves(cfg):
    values = [(10, 2), (20, 3), (30, 4), (40, 5), (50, 6), (60, 7)]
    rows = [[f"Item {i}", str(a), str(b)] for i, (a, b) in enumerate(values)]
    rows.append(["Total", str(sum(a for a, _ in values)), str(sum(b for _, b in values))])
    table = SourceTable(table_id="t_tot", page=1, header=["Item", "Q1", "Q2"], raw_cells=rows)
    _, data = run(doc_with(table), cfg)
    frame = data.tables["t_tot"]
    assert frame.at[6, "Q1"] == pytest.approx(frame["Q1"].iloc[:6].sum())
    assert frame.at[6, "Q2"] == pytest.approx(frame["Q2"].iloc[:6].sum())
    assert data.lineage["t_tot.Q1"] == "rollup"


def test_foreign_keys_resolve_to_generated_parent_keys(cfg):
    customers = SourceTable(table_id="t_cust", page=1, header=["customer_id", "name"],
                            raw_cells=[["C-101", "Ann"], ["C-102", "Bob"], ["C-103", "Cid"]])
    orders = SourceTable(table_id="t_ord", page=2, header=["order_id", "customer_id", "amount"],
                         raw_cells=[[f"O-{101 + i}", ["C-101", "C-102", "C-101", "C-103", "C-102", "C-101"][i],
                                     str(10 * (i + 1))] for i in range(6)])
    _, data = run(doc_with(customers, orders), cfg)
    parents = set(data.tables["t_cust"]["customer_id"])
    children = set(data.tables["t_ord"]["customer_id"])
    assert children <= parents


def test_summary_equals_the_detail_aggregate(cfg):
    detail_rows = [[region, str(value)] for region, value in
                   [("N", 10), ("N", 20), ("S", 30), ("S", 40), ("E", 50), ("E", 60)]]
    detail = SourceTable(table_id="t_detail", page=1, header=["Region", "Revenue"],
                         raw_cells=detail_rows)
    summary = SourceTable(table_id="t_summary", page=2, header=["Region", "Total Revenue"],
                          raw_cells=[["N", "30"], ["S", "70"], ["E", "110"]])
    _, data = run(doc_with(detail, summary), cfg)
    expected = data.tables["t_detail"].groupby("Region")["Revenue"].sum()
    for label, value in zip(data.tables["t_summary"]["Region"], data.tables["t_summary"]["Total Revenue"]):
        assert value == pytest.approx(expected.get(label, 0.0))


def test_same_seed_gives_an_identical_dataset(cfg):
    doc = sales_document()
    report = infer(doc, cfg)
    plan = plan_privacy(doc, report, cfg)
    first = generate(doc, report, plan, cfg, np.random.default_rng(7))
    second = generate(doc, report, plan, cfg, np.random.default_rng(7))
    for table_id, frame in first.tables.items():
        assert frame.equals(second.tables[table_id])


def test_display_table_is_rounded_and_shares_the_shape(cfg):
    doc = sales_document()
    _, data = run(doc, cfg)
    assert set(data.display) == set(data.tables)
    assert data.display["t_sales"].shape == data.tables["t_sales"].shape


def test_trend_is_preserved_in_the_generated_series(cfg):
    rows = [[f"FY{16 + i}A", str(100 + 10 * i)] for i in range(10)]
    table = SourceTable(table_id="t_trend", page=1, header=["Period", "Revenue"], raw_cells=rows)
    doc = doc_with(table)
    report = infer(doc, cfg)
    assert any(p.kind is PatternKind.TREND for p in report.patterns)
    data = generate(doc, report, plan_privacy(doc, report, cfg), cfg, np.random.default_rng(5))
    assert classify_trend(list(data.tables["t_trend"]["Revenue"]), cfg.thresholds)[0] == "up"
    assert list(data.tables["t_trend"]["Period"]) == [row[0] for row in rows]
    assert data.unmet_targets == [] and data.validation.ok
