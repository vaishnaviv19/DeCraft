# tests/validation/test_checks.py
import json

import numpy as np
import pandas as pd

from src.contracts import SourceDocument, SourceTable, ValidationReport
from src.contracts.dataset import CanonicalDataset
from src.generation import generate
from src.inference import infer
from src.privacy import plan_privacy
from src.validation import validate
from tests.conftest import FakeLLM


def doc_with(*tables, text="text"):
    return SourceDocument(document_sha256="abc12345", tables=list(tables), text_blocks=[text])


def dataset(frames, keys=None):
    return CanonicalDataset(dataset_id="d", schema_version="2.3", seed=42, tables=frames, display={},
                            key_columns=keys or {}, rules=[], facts={}, charts={}, lineage={},
                            validation=ValidationReport(ok=True))


def checks(report) -> set[str]:
    return {v.check for v in report.violations}


def sales_doc():
    rows = [[f"A-{101 + i}", "North", str(u), str(p), str(u * p)]
            for i, (u, p) in enumerate([(120, 45), (80, 50), (150, 42), (95, 48), (110, 46), (70, 52)])]
    return doc_with(SourceTable(table_id="t_sales", page=1,
                                header=["Ref", "Region", "Units", "Unit Price", "Revenue"],
                                raw_cells=rows))


def test_nan_and_infinity_are_errors(cfg):
    report = infer(sales_doc(), cfg)
    data = dataset({"t_sales": pd.DataFrame({"Ref": list("ABCDEF"), "Revenue": [1.0, np.nan, 3.0, np.inf, 5.0, 6.0]})},
                   keys={"t_sales": ["Ref"]})
    found = checks(validate(data, report, cfg))
    assert "invalid_number" in found


def test_duplicate_primary_key_is_an_error(cfg):
    report = infer(sales_doc(), cfg)
    data = dataset({"t_sales": pd.DataFrame({"Ref": ["A", "A", "B", "C", "D", "E"],
                                             "Revenue": [1.0] * 6})}, keys={"t_sales": ["Ref"]})
    assert "primary_key" in checks(validate(data, report, cfg))


def test_broken_foreign_key_is_an_error(cfg):
    customers = SourceTable(table_id="t_cust", page=1, header=["customer_id", "name"],
                            raw_cells=[["C-101", "Ann"], ["C-102", "Bob"], ["C-103", "Cid"]])
    orders = SourceTable(table_id="t_ord", page=2, header=["order_id", "customer_id", "amount"],
                         raw_cells=[[f"O-{101 + i}", ["C-101", "C-102", "C-101"][i % 3], "10"]
                                    for i in range(6)])
    doc = doc_with(customers, orders)
    report = infer(doc, cfg)
    frames = {"t_cust": pd.DataFrame({"customer_id": ["C-900", "C-901", "C-902"], "name": ["a", "b", "c"]}),
              "t_ord": pd.DataFrame({"order_id": [f"O-{i}" for i in range(6)],
                                     "customer_id": ["C-900"] * 5 + ["C-999"], "amount": [1.0] * 6})}
    assert "foreign_key" in checks(validate(dataset(frames, {"t_cust": ["customer_id"]}), report, cfg))


def test_formula_mismatch_is_an_error(cfg, gateway_for):
    reply = json.dumps({"domain": "retail", "domain_confidence": 0.8, "columns": [],
                        "proposals": [{"kind": "formula", "columns": ["Revenue", "Units", "Unit Price"],
                                       "params": {"expression": "[Revenue] = [Units] * [Unit Price]"}}]})
    llm_cfg = cfg.model_copy(update={"llm_enabled": True})
    doc = sales_doc()
    report = infer(doc, llm_cfg, gateway=gateway_for(FakeLLM([reply])))
    frame = pd.DataFrame({"Ref": list("ABCDEF"), "Units": [1.0] * 6, "Unit Price": [2.0] * 6,
                          "Revenue": [3.0] * 6})              # should be 2.0
    found = checks(validate(dataset({"t_sales": frame}), report, llm_cfg))
    assert "formula" in found


def test_negative_value_is_an_error(cfg):
    doc = sales_doc()
    report = infer(doc, cfg)
    frame = pd.DataFrame({"Ref": list("ABCDEF"), "Units": [-5.0] + [1.0] * 5, "Unit Price": [2.0] * 6,
                          "Revenue": [2.0] * 6, "Region": ["North"] * 6})
    found = checks(validate(dataset({"t_sales": frame}), report, cfg, source=doc))
    assert "negative_value" in found


def test_total_row_must_equal_the_sum_of_its_leaves(cfg):
    rows = [[f"Item {i}", str(10 * (i + 1)), str(2 * (i + 1))] for i in range(6)]
    rows.append(["Total", "210", "42"])
    doc = doc_with(SourceTable(table_id="t_tot", page=1, header=["Item", "Q1", "Q2"], raw_cells=rows))
    report = infer(doc, cfg)
    frame = pd.DataFrame({"Item": [f"Item {i}" for i in range(6)] + ["Total"],
                          "Q1": [10.0] * 6 + [99.0], "Q2": [2.0] * 6 + [12.0]})
    assert "total_link" in checks(validate(dataset({"t_tot": frame}), report, cfg))


def test_generated_dataset_passes_its_own_validation(cfg):
    doc = sales_doc()
    report = infer(doc, cfg)
    data = generate(doc, report, plan_privacy(doc, report, cfg), cfg, np.random.default_rng(3))
    assert data.validation.ok and data.validation.repairs == []
