# tests/rendering/test_charts.py
import json

import numpy as np
import pytest

from src.contracts import ChartSpec, SourceDocument, SourceTable
from src.generation import generate
from src.inference import infer
from src.privacy import plan_privacy
from src.rendering._charts import (assert_chart_matches_table, chart_labels, chart_series,
                                   draw_svg, write_sidecar)


def dataset(cfg):
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
    data.charts = {"c1": ChartSpec(chart_id="c1", table_id="t_sales", label_column="Ref",
                                   series_columns=["Revenue"])}
    return data


def test_an_unregistered_chart_cannot_be_drawn(cfg):
    with pytest.raises(KeyError):
        chart_series(dataset(cfg), "missing")


def test_sidecar_equals_the_display_table_and_the_svg_is_written(cfg, tmp_path):
    data = dataset(cfg)
    sidecar = write_sidecar(data, "c1", tmp_path / "c1.json")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    expected = [float(v) for v in data.display["t_sales"]["Revenue"]]
    assert payload["series"]["Revenue"] == expected
    assert chart_labels(data, "c1") == [str(v) for v in data.display["t_sales"]["Ref"]]
    assert assert_chart_matches_table(payload["series"], data.display["t_sales"], ["Revenue"])
    svg = draw_svg(data, "c1", tmp_path / "c1.svg")
    assert svg.exists() and svg.stat().st_size > 500


def test_a_series_missing_from_the_display_table_raises(cfg):
    data = dataset(cfg)
    data.charts["c2"] = ChartSpec(chart_id="c2", table_id="t_sales", label_column="Ref",
                                  series_columns=["Ghost"])
    with pytest.raises(KeyError):
        chart_series(data, "c2")
