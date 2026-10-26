# tests/rendering/test_charts.py
import json

import numpy as np
import pytest

from src.contracts import ChartSpec, SourceDocument, SourceTable
from src.generation import generate
from src.inference import infer
from src.privacy import plan_privacy
from src.rendering._charts import (assert_chart_matches_table, assert_sidecar_matches_display,
                                   chart_labels, chart_series, draw_svg, write_sidecar)


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


def test_a_rows_sidecar_is_checked_against_its_line_items():
    import pandas as pd

    display = pd.DataFrame({"Consolidated (cr)": ["Sales", "EBITDA"],
                            "FY24A": [10.0, 2.0], "FY25E": [12.0, 3.0]})
    payload = {"orientation": "rows", "label_column": "Consolidated (cr)",
               "labels": ["FY24A", "FY25E"],
               "series": {"Sales": [10.0, 12.0], "EBITDA": [2.0, 3.0]}}
    assert assert_sidecar_matches_display(payload, display)
    payload["series"]["Sales"] = [10.0, 99.0]
    with pytest.raises(AssertionError):
        assert_sidecar_matches_display(payload, display)


def test_a_rows_sidecar_naming_an_unknown_line_item_fails_the_gate():
    import pandas as pd

    display = pd.DataFrame({"Consolidated (cr)": ["Sales"], "FY24A": [10.0]})
    payload = {"orientation": "rows", "label_column": "Consolidated (cr)",
               "labels": ["FY24A"], "series": {"Ghost": [10.0]}}
    with pytest.raises(AssertionError):
        assert_sidecar_matches_display(payload, display)


def test_a_text_number_table_gets_a_chart_with_its_sub_header_as_axis_labels():
    """Live run 2026-10-26: the shareholding chart's axis read 'Unnamed: 1/2/3'."""
    import pandas as pd
    from src.generation._charts import text_number_chart
    frame = pd.DataFrame({"Company Data": ["Beta", "Shareholding %", "Promoters", "Public", "Others"],
                          "Unnamed: 1": ["", "Q3FY24", "49.4", "30.0", "0.9"],
                          "Unnamed: 2": ["", "Q4FY24", "58.7", "35.9", "0.9"],
                          "Unnamed: 3": ["1.3", "Q1FY25", "59.9", "35.3", "0.9"]})
    spec = text_number_chart("c", "t", frame, "Company Data")
    assert spec.series_rows == ["Promoters", "Public", "Others"]
    assert spec.x_labels == ["Q3FY24", "Q4FY24", "Q1FY25"]
