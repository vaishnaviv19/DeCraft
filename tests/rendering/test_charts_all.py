# tests/rendering/test_charts_all.py
import json

import numpy as np
import pandas as pd

from src.config import load_config
from src.contracts import SourceDocument, SourceTable
from src.contracts.dataset import empty_dataset
from src.generation import generate, register_all_charts
from src.generation._charts import choose_chart_kind
from src.inference import infer
from src.privacy import plan_privacy
from src.rendering._charts import (assert_chart_matches_table, contact_sheet,
                                   draw_png, draw_svg, write_sidecar)


def sales_data(cfg):
    rows = [[f"A-{101 + i}", "North", str(u), str(p), str(u * p)]
            for i, (u, p) in enumerate([(120, 45), (80, 50), (150, 42), (95, 48), (110, 46), (70, 52)])]
    doc = SourceDocument(document_sha256="charts12345", tables=[
        SourceTable(table_id="t_sales", page=1,
                    header=["Ref", "Region", "Units", "Unit Price", "Revenue"], raw_cells=rows)])
    report = infer(doc, cfg)
    return generate(doc, report, plan_privacy(doc, report, cfg), cfg,
                    np.random.default_rng(cfg.seed))


def test_every_displayed_table_is_charted_and_the_svg_has_its_labels(tmp_path):
    cfg = load_config(llm_enabled=False)
    data = sales_data(cfg)
    data.charts = register_all_charts(data, None)
    assert {spec.table_id for spec in data.charts.values()} == set(data.display)
    chart_id = next(iter(data.charts))
    spec = data.charts[chart_id]
    svg = draw_svg(data, chart_id, tmp_path / f"{chart_id}.svg")
    content = svg.read_text(encoding="utf-8")
    assert "<text" in content
    for label in data.display[spec.table_id][spec.label_column].astype(str).head(4):
        assert label in content
    sidecar = write_sidecar(data, chart_id, tmp_path / f"{chart_id}.json")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert assert_chart_matches_table(payload["series"], data.display[spec.table_id],
                                      spec.series_columns)


def test_png_and_contact_sheet_are_written(tmp_path):
    cfg = load_config(llm_enabled=False)
    data = sales_data(cfg)
    data.charts = register_all_charts(data, None)
    paths = [draw_png(data, chart_id, tmp_path / f"{chart_id}.png") for chart_id in data.charts]
    sheet = contact_sheet(paths, tmp_path / "contact_charts.png")
    assert sheet is not None and sheet.exists() and sheet.stat().st_size > 1000


def test_a_donut_and_a_horizontal_bar_render(tmp_path):
    data = empty_dataset()
    data.tables = {"pie": pd.DataFrame({"Segment": ["A", "B", "C"], "Share": [30.0, 30.0, 40.0]}),
                   "cats": pd.DataFrame({"Region": list("ABCDEF"), "Value": [3.0, 1.0, 2.0, 5.0, 4.0, 6.0]})}
    data.display = {name: frame.copy() for name, frame in data.tables.items()}
    data.charts = register_all_charts(data, None)
    assert choose_chart_kind(data, "pie") == "donut"
    assert choose_chart_kind(data, "cats") == "horizontal_bar"
    for chart_id, spec in data.charts.items():
        assert spec.kind in {"donut", "horizontal_bar"}
        content = draw_svg(data, chart_id, tmp_path / f"{chart_id}.svg").read_text(encoding="utf-8")
        assert "<text" in content
