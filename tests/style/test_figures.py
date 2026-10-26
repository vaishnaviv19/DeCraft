# tests/style/test_figures.py
import io
import json
import logging
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from src.config import load_config
from src.style import analyse_figures, build_templates, extract_style
from src.style._figures import recovered_to_source_table

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "input" / "sample_input_1.pdf"
CFG = load_config(llm_enabled=False)


def figure_pdf(path: Path) -> Path:
    buffer = io.BytesIO()
    Image.new("RGB", (400, 240), "white").save(buffer, format="JPEG")
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 100), "Body text for the page", fontsize=10, fontname="helv")
    page.insert_image(pymupdf.Rect(72, 150, 472, 390), stream=buffer.getvalue())
    document.save(path)
    document.close()
    return path


def setup(path: Path):
    cfg = load_config(llm_enabled=False)
    style = extract_style(path, cfg)
    return cfg, build_templates(path, None, style, cfg)


def figure_block_id(templates) -> str:
    return next(b.block_id for page in templates for b in page.blocks
                if b.kind in {"photo", "logo", "chart"})


def reply(figures: list[dict]) -> str:
    return json.dumps({"figures": figures})


def test_valid_reply_labels_the_figure(tmp_path, gateway_for):
    from tests.conftest import FakeLLM
    path = figure_pdf(tmp_path / "figure.pdf")
    cfg, templates = setup(path)
    figure_id = figure_block_id(templates)
    label = reply([{"figure_id": figure_id, "kind": "chart", "chart_kind": "bar",
                    "has_legend": True, "has_data_labels": False, "description": ""}])
    chart = json.dumps({"figure_id": figure_id, "categories": ["A", "B"],
                        "series": [{"name": "S1", "values": [10, 20]}], "unit": "USD",
                        "confidence": 0.8})
    gateway = gateway_for(FakeLLM([label, chart]))
    figures, recovered = analyse_figures(path, templates, cfg, gateway)
    assert figures[0].kind == "chart" and figures[0].chart_kind == "bar" and figures[0].has_legend
    assert len(recovered) == 1 and recovered[0].categories == ["A", "B"]
    table = recovered_to_source_table(recovered[0])
    assert table.table_id == f"rc_{figure_id}"
    assert table.header == ["Category", "S1"] and table.raw_cells[0] == ["A", "10"]


def test_a_missing_id_falls_back_to_the_rules(tmp_path, gateway_for, caplog):
    from tests.conftest import FakeLLM
    caplog.set_level(logging.WARNING, logger="decraft.style")
    path = figure_pdf(tmp_path / "missing.pdf")
    cfg, templates = setup(path)
    figures, _ = analyse_figures(path, templates, cfg,
                                 gateway_for(FakeLLM([reply([])])))
    assert figures[0].kind == "photo"
    assert any("did not answer" in message for message in caplog.messages)


def test_without_a_gateway_every_figure_gets_a_rule_label(tmp_path):
    path = figure_pdf(tmp_path / "nogateway.pdf")
    cfg, templates = setup(path)
    figures, recovered = analyse_figures(path, templates, cfg, None)
    assert figures and figures[0].kind in {"photo", "logo"} and recovered == []


def test_series_lengths_must_match_the_categories(tmp_path, gateway_for, caplog):
    from tests.conftest import FakeLLM
    caplog.set_level(logging.WARNING, logger="decraft.style")
    path = figure_pdf(tmp_path / "lengths.pdf")
    cfg, templates = setup(path)
    figure_id = figure_block_id(templates)
    label = reply([{"figure_id": figure_id, "kind": "chart", "chart_kind": "line"}])
    chart = json.dumps({"figure_id": figure_id, "categories": ["A", "B"],
                        "series": [{"name": "S1", "values": [1]}], "unit": None,
                        "confidence": 0.9})
    _, recovered = analyse_figures(path, templates, cfg,
                                   gateway_for(FakeLLM([label, chart])))
    assert recovered == [] and any("rejected" in message for message in caplog.messages)


def test_a_low_confidence_recovery_is_rejected(tmp_path, gateway_for):
    from tests.conftest import FakeLLM
    path = figure_pdf(tmp_path / "confidence.pdf")
    cfg, templates = setup(path)
    figure_id = figure_block_id(templates)
    label = reply([{"figure_id": figure_id, "kind": "chart", "chart_kind": "bar"}])
    chart = json.dumps({"figure_id": figure_id, "categories": ["A"],
                        "series": [{"name": "S1", "values": [1]}], "unit": None,
                        "confidence": 0.2})
    _, recovered = analyse_figures(path, templates, cfg,
                                   gateway_for(FakeLLM([label, chart])))
    assert recovered == []


def test_the_real_sample_gets_rule_labels_without_a_gateway():
    """v3.3 F-07: the sample's two vector price charts are recovered from geometry, no model needed."""
    style = extract_style(SAMPLE, CFG)
    templates = build_templates(SAMPLE, None, style, CFG)
    figures, recovered = analyse_figures(SAMPLE, templates, CFG, None)
    assert figures and len(recovered) == 2 and all(chart.confidence >= 0.9 for chart in recovered)
    assert all(figure.kind in {"photo", "logo", "chart", "icon", "other"} for figure in figures)
