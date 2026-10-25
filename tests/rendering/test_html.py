# tests/rendering/test_html.py
from pathlib import Path

import numpy as np

from src.contracts import ChartSpec, SourceDocument, SourceTable
from src.generation import generate
from src.inference import infer
from src.narrative import write
from src.privacy import plan_privacy
from src.rendering import render
from src.rendering._html import build_html

PLAN = {"sections": [{"section_id": "s1", "title": "Overview", "target_word_count": 200,
                      "tables": [{"table_id": "t_sales"}]}]}


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


def test_html_contains_the_sections_tables_and_metadata(cfg):
    data = dataset(cfg)
    narrative = write(data, PLAN, cfg)
    html = build_html(data, narrative, {"entity": {"entity_name": "Acme"}})
    assert "Acme" in html and "Overview" in html
    assert narrative.sections[0].text[:40] in html
    assert "{{" not in html and "decraft:synthetic" in html
    assert "<table" in html


def test_render_writes_html_charts_and_reports_the_pdf_status(cfg, tmp_path):
    data = dataset(cfg, chart=True)
    narrative = write(data, PLAN, cfg)
    result = render(data, narrative, {"entity": {"entity_name": "Acme"}}, cfg, tmp_path)
    assert Path(result.html_path).exists()
    assert set(result.chart_paths) == {"c1"}
    assert (tmp_path / "charts" / "c1.svg").exists()
    assert (tmp_path / "charts" / "c1.json").exists()
    assert "pdf" in result.qa
