# tests/privacy/test_interface.py
import pandas as pd

from src.contracts import Fact, SensitivityClass, SourceDocument, SourceTable, ValidationReport
from src.contracts.dataset import CanonicalDataset, empty_dataset
from src.inference import infer
from src.privacy import audit_privacy, plan_privacy

SMALL = SourceTable(table_id="t_small", page=1, header=["Company", "Email", "Revenue"],
                    raw_cells=[["Acme", "a@x.com", "100.00"],
                               ["Acme", "b@y.org", "200.00"],
                               ["Acme", "c@z.net", "300.00"]])
BIG = SourceTable(table_id="t_big", page=2, header=["Region", "Revenue"],
                  raw_cells=[["North", "10.00"], ["South", "20.00"], ["East", "30.00"],
                             ["West", "40.00"], ["North", "50.00"], ["South", "60.00"]])


def document():
    return SourceDocument(document_sha256="abc", entity_name="Acme",
                          tables=[SMALL, BIG], text_blocks=["As at 31 March 2024"])


def test_plan_covers_every_column_and_lists_tiny_tables(cfg):
    doc = document()
    report = infer(doc, cfg)
    plan = plan_privacy(doc, report, cfg)
    assert len(plan.decisions) == 5
    assert plan.tiny_tables == ["t_small"]
    email = next(d for d in plan.decisions if d.column == "Email")
    assert email.risk is SensitivityClass.DIRECT_IDENTIFIER and email.method == "regenerate"
    company = next(d for d in plan.decisions if d.column == "Company")
    assert company.exempt and company.method == "preserve"


def test_bad_tables_get_no_decisions(cfg):
    bad = SourceTable(table_id="t_bad", page=1, header=["A", "B"], raw_cells=[["1"], ["2", "x"]])
    doc = SourceDocument(document_sha256="abc", tables=[bad])
    assert plan_privacy(doc, infer(doc, cfg), cfg).decisions == []


def _dataset(doc, report, cfg, scale: float) -> CanonicalDataset:
    tables = {
        "t_small": pd.DataFrame({"Company": ["Acme"] * 3,
                                 "Email": ["q@r.com", "s@t.io", "u@v.net"],
                                 "Revenue": [100.0 * scale, 200.0 * scale, 300.0 * scale]}),
        "t_big": pd.DataFrame({"Region": ["North", "South", "East", "West", "North", "South"],
                               "Revenue": [10.0 * scale, 20.0 * scale, 30.0 * scale,
                                           40.0 * scale, 50.0 * scale, 60.0 * scale]}),
    }
    facts = {"rev": Fact(key="rev", value=tables["t_big"]["Revenue"].sum(), kind="number",
                         source="t_big.Revenue:sum")}
    return CanonicalDataset(dataset_id="d", schema_version="2.2", seed=cfg.seed, tables=tables,
                            display={}, key_columns={}, rules=[], facts=facts, charts={}, lineage={},
                            validation=ValidationReport(ok=True), source_profiles=report.profiles)


def test_audit_is_clean_for_fresh_data(cfg):
    doc = document()
    report = infer(doc, cfg)
    data = _dataset(doc, report, cfg, scale=1.5)
    privacy = audit_privacy(doc, data, plan_privacy(doc, report, cfg), cfg)
    assert privacy.ok and privacy.violations == []
    assert "Synthetic data is not automatically anonymous." in privacy.limitations
    assert "No formal differential privacy mechanism is implemented or claimed." in privacy.limitations


def test_audit_flags_reused_identifiers_and_close_values(cfg):
    doc = document()
    report = infer(doc, cfg)
    data = _dataset(doc, report, cfg, scale=1.0)               # values identical to the source
    data.tables["t_small"] = data.tables["t_small"].assign(
        Email=["a@x.com", "b@y.org", "c@z.net"])               # and the source emails reused
    privacy = audit_privacy(doc, data, plan_privacy(doc, report, cfg), cfg)
    checks = {v.check for v in privacy.violations}
    assert not privacy.ok
    assert {"identifier_reuse", "value_distance"} <= checks


def test_audit_accepts_an_empty_dataset(cfg):
    doc = document()
    report = infer(doc, cfg)
    privacy = audit_privacy(doc, empty_dataset(), plan_privacy(doc, report, cfg), cfg)
    assert privacy.ok and privacy.checks_run
