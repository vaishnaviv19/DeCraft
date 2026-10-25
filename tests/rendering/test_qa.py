# tests/rendering/test_qa.py
import numpy as np

from src.contracts import ChartSpec, SourceDocument, SourceTable
from src.contracts.dataset import empty_dataset
from src.generation import generate
from src.inference import infer
from src.narrative import write
from src.privacy import plan_privacy
from src.rendering import _prepare_weasyprint, render
from src.rendering._qa import blocks_within, run_qa

SECTIONS = 6


def plan() -> dict:
    return {"sections": [{"section_id": f"s{i}", "title": f"Review {i}",
                          "target_word_count": 200, "tables": [{"table_id": "t_sales"}]}
                         for i in range(SECTIONS)]}


def dataset(cfg, chart: bool = False):
    values = [(120, 45), (80, 50), (150, 42), (95, 48), (110, 46), (70, 52)]
    rows = [[f"A-{101 + i}", "North", str(u), str(p), str(u * p)]
            for i, (u, p) in enumerate(values)]
    doc = SourceDocument(document_sha256="abc", text_blocks=["text"],
                         tables=[SourceTable(table_id="t_sales", page=1,
                                             header=["Ref", "Region", "Units", "Unit Price", "Revenue"],
                                             raw_cells=rows)])
    report = infer(doc, cfg)
    data = generate(doc, report, plan_privacy(doc, report, cfg), cfg,
                    np.random.default_rng(cfg.seed))
    if chart:
        data.charts = {"c1": ChartSpec(chart_id="c1", table_id="t_sales", label_column="Ref",
                                       series_columns=["Revenue"])}
    return data


def test_blocks_outside_the_page_are_detected():
    assert blocks_within((0, 0, 100, 100), [(1, 1, 90, 90)]) is True
    assert blocks_within((0, 0, 100, 100), [(1, 1, 120, 90)]) is False


def test_a_full_report_passes_rendered_pdf_qa(cfg, tmp_path):
    data = dataset(cfg, chart=True)
    narrative = write(data, plan(), cfg)
    assert narrative.word_count >= 1000
    result = render(data, narrative, {}, cfg, tmp_path)
    assert result.pdf_path is not None and result.pages >= 1
    qa = result.qa["rendered_pdf_qa"]
    assert qa["ok"], qa
    assert qa["checks"]["no_unresolved_placeholders"]
    assert qa["checks"]["chart_sidecars"] and qa["checks"]["charts_captioned"]
    assert (tmp_path / "pages" / "page_001.png").exists()
    assert qa["manual_review_required"]


def test_a_leftover_placeholder_fails_qa(tmp_path):
    _prepare_weasyprint()
    from weasyprint import HTML
    pdf = tmp_path / "leftover.pdf"
    HTML(string="<p>Revenue was {{ revenue.total }} in the period.</p>").write_pdf(str(pdf))
    result = run_qa(pdf, empty_dataset(), tmp_path, min_words=0)
    assert result.checks["no_unresolved_placeholders"] is False and result.ok is False
