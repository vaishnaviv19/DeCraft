# tests/generation/test_facts.py
import numpy as np

from src.contracts import SourceDocument, SourceTable
from src.generation import generate
from src.generation._facts import compute_facts
from src.inference import infer
from src.privacy import plan_privacy


def sales_document():
    values = [(120, 45), (80, 50), (150, 42), (95, 48), (110, 46), (70, 52)]
    rows = [[f"A-{101 + i}", region, str(u), str(p), str(u * p)]
            for i, ((u, p), region) in enumerate(zip(values, ["North", "South", "North",
                                                              "East", "South", "West"]))]
    table = SourceTable(table_id="t_sales", page=1,
                        header=["Ref", "Region", "Units", "Unit Price", "Revenue"],
                        raw_cells=rows)
    return SourceDocument(document_sha256="abc12345", tables=[table], text_blocks=["text"])


def test_totals_ranking_and_labels_come_from_the_display_table(cfg):
    doc = sales_document()
    report = infer(doc, cfg)
    data = generate(doc, report, plan_privacy(doc, report, cfg), cfg,
                    np.random.default_rng(cfg.seed))
    facts = compute_facts(data, report.patterns)
    display = data.display["t_sales"]
    assert facts["t_sales.Revenue.total"].value == display["Revenue"].sum()
    assert facts["t_sales.Revenue.largest"].value == display["Revenue"].max()
    assert facts["t_sales.Revenue.largest_label"].value in set(display["Ref"].astype(str))
    assert facts["t_sales.first_label"].value == str(display["Ref"].iloc[0])
    assert all(f.key == key for key, f in facts.items())


def test_no_growth_fact_without_a_trend(cfg):
    doc = sales_document()
    report = infer(doc, cfg)
    data = generate(doc, report, plan_privacy(doc, report, cfg), cfg,
                    np.random.default_rng(cfg.seed))
    assert "t_sales.Revenue.growth" not in compute_facts(data, report.patterns)
