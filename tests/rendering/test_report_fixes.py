# tests/rendering/test_report_fixes.py
"""M8 fixes: charts from the plan, facts in time order and source units, text that matches the tables,
source-like HTML with nothing saying the data is synthetic, and PDF file metadata."""
import json

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.contracts import Fact, NarrativeDoc, NarrativeSection, SourceDocument, SourceTable
from src.core.facts import compute_facts
from src.generation import generate, register_charts
from src.inference import infer
from src.narrative import write
from src.narrative._placeholders import StrayNumber, resolve, stray_numbers
from src.narrative._writer import catalogue, contradicts
from src.privacy import audit_privacy, plan_privacy
from src.rendering import render
from src.rendering._charts import chart_labels, chart_series, sidecar_matches, write_sidecar
from src.rendering._html import build_html, format_number

CFG = load_config(llm_enabled=False)
YEARS = ["FY21", "FY22", "FY23", "FY24", "FY25"]
PL = SourceTable(table_id="pl", page=1, caption="Income statement (Rs cr)",
                 header=["Line item", "FY25", "FY24", "FY23", "FY22", "FY21"], raw_cells=[
                     ["Revenue", "1,350", "1,260", "1,170", "1,080", "1,000"],
                     ["Cost of sales", "770", "730", "690", "640", "600"],
                     ["Gross profit", "580", "530", "480", "440", "400"],
                     ["Tax Rate (%)", "25.0", "24.0", "25.5", "26.0", "24.5"],
                     ["Other items", "(20)", "(15)", "(12)", "(10)", "(8)"]])
SALES = SourceTable(table_id="sales", page=2, header=["Region", "Units", "Margin %"], raw_cells=[
    [r, str(u), f"{m:.1f}"] for r, u, m in zip(["North", "South", "East", "West", "Central", "Coast"],
                                              [120, 80, 150, 95, 110, 70], [12.1, 9.8, 14.2, 11.0, 10.5, 13.3])])
PLAN = {"sections": [
    {"section_id": "s1", "title": "Page 1 - Acme Content", "source_page": 1, "target_word_count": 120,
     "tables": [{"table_id": "pl"}], "charts": [{"chart_id": "c_pl", "type": "bar", "data_source_table_id": "pl"}]},
    {"section_id": "s2", "title": "Regional review", "target_word_count": 120,
     "tables": [{"table_id": "sales"}], "charts": [{"chart_id": "c_sales", "type": "pie",
                                                     "data_source_table_id": "sales"}]}]}


@pytest.fixture(scope="module")
def built():
    doc = SourceDocument(document_sha256="m8fix123", entity_name="Acme Power Ltd", tables=[PL, SALES],
                         text_blocks=["Acme Power Ltd annual review"])
    report = infer(doc, CFG)
    plan = plan_privacy(doc, report, CFG)
    data = generate(doc, report, plan, CFG, np.random.default_rng(3))
    data.charts = register_charts(data, PLAN)
    return doc, report, plan, data


def shown(plan, node, label):
    """The label as printed: E-35 replaces non-generic labels (LLM off: '<column> A (%)')."""
    return plan.label_maps.get(node, {}).get(label, label)


# ---------------------------------------------------------------- charts come from the plan
def test_every_planned_chart_is_registered_with_the_right_shape(built):
    _, _, plan, data = built
    pl, sales = data.charts["c_pl"], data.charts["c_sales"]
    tax = shown(plan, "pl.Line item", "Tax Rate (%)")
    assert tax == "Tax Rate (%)" and tax.endswith("(%)")   # a measure of the field keeps its unit (E-27)
    assert pl.orientation == "rows" and pl.kind == "line" and pl.series_columns == YEARS
    assert tax not in pl.series_rows and "Revenue" in pl.series_rows   # amounts only
    assert shown(plan, "pl.Line item", "Other items") != "Other items"  # a name-like label is fabricated
    assert sales.kind == "pie" and sales.series_columns == ["Units"]          # 6 slices of an amount
    assert "by Region" in sales.title and not sales.title.startswith("c_")


def test_a_statement_chart_reads_one_row_across_the_periods(built, tmp_path):
    *_, data = built
    assert chart_labels(data, "c_pl") == YEARS
    revenue = data.display["pl"].set_index("Line item").loc["Revenue", YEARS].astype(float).tolist()
    assert chart_series(data, "c_pl")["Revenue"] == revenue
    path = write_sidecar(data, "c_pl", tmp_path / "c_pl.json")
    assert sidecar_matches(data, "c_pl", path)


# ---------------------------------------------------------------- facts: time order, units, meaning
def test_statement_facts_have_the_right_sign_and_source_units(built):
    _, _, plan, data = built
    f = data.facts
    tax = shown(plan, "pl.Line item", "Tax Rate (%)")
    assert f["pl.row:Revenue.growth"].value > 0                         # FY21 -> FY25, not row order
    assert 500 < f["pl.row:Revenue.latest"].value < 3000                 # source units, not base units
    assert f"pl.row:{tax}.growth" not in f                               # a rate has no growth %
    assert f[f"pl.row:{tax}.latest"].unit == "%"
    assert f["pl.first_period"].value == "FY21" and f["pl.last_period"].value == "FY25"


def test_rates_are_averaged_not_added_up(built):
    *_, data = built
    assert "sales.Margin %.total" not in data.facts and "sales.Margin %.average" in data.facts
    assert "sales.Units.total" in data.facts


def test_growth_follows_time_for_newest_first_tables():
    t = SourceTable(table_id="r", page=1, header=["Year", "Revenue"],
                    raw_cells=[[f"FY{y}", str(v)] for y, v in zip(range(25, 15, -1),
                                                                   [200, 188, 171, 160, 149, 140, 128, 119, 110, 100])])
    doc = SourceDocument(document_sha256="m8fix123", tables=[t], text_blocks=["x"])
    report = infer(doc, CFG)
    data = generate(doc, report, plan_privacy(doc, report, CFG), CFG, np.random.default_rng(1))
    assert data.facts["r.Revenue.direction"].value == "up" and data.facts["r.Revenue.growth"].value > 0


# ---------------------------------------------------------------- text matches the tables
def test_placeholders_use_the_columns_own_format():
    facts = {"m": Fact(key="m", value=12.46, kind="number", decimals=2, source="x"),
             "r": Fact(key="r", value=1250.4, kind="number", unit="$", decimals=1, source="x"),
             "g": Fact(key="g", value=-5.04, kind="number", unit="%", decimals=1, source="x")}
    assert resolve("{{ m }} {{ r }} fell by {{ g|%:1|abs }}", facts) == "12.46 $1,250.4 fell by 5.0%"


def test_period_words_are_allowed_but_other_digits_are_not():
    assert stray_numbers("Revenue grew in FY24 and Q3 and H1FY25.") == []
    with pytest.raises(StrayNumber):
        resolve("Revenue grew by 12 percent", {})


def test_the_writer_sees_directions_not_numbers_and_contradictions_are_rejected(built):
    *_, data = built
    entry = next(e for e in catalogue(data.facts, list(data.facts)) if e["key"] == "pl.row:Revenue.growth")
    assert entry["moved"] == "rose" and "value" not in entry
    assert contradicts("Revenue fell by {{ pl.row:Revenue.growth|%:1|abs }}.", data.facts)
    assert not contradicts("Revenue rose by {{ pl.row:Revenue.growth|%:1 }}.", data.facts)


def test_fallback_text_comes_from_the_facts_and_agrees_with_them(built):
    *_, data = built
    doc = write(data, PLAN, CFG)
    text = doc.sections[0].text
    assert "Revenue rose by" in text or "Revenue ended" in text or "Revenue" in text
    assert "fell" not in text.split("Revenue")[1][:20]                  # never 'Revenue fell' for a rise
    assert "{{" not in text


# ---------------------------------------------------------------- HTML looks like the source
def test_html_says_nothing_about_being_synthetic_and_keeps_the_source_layout(built):
    doc, _, _, data = built
    narrative = write(data, PLAN, CFG)
    html = build_html(data, narrative, {"entity": {"entity_name": "Acme Power Ltd"}}, PLAN, doc)
    low = html.lower()
    assert not any(w in low for w in ("synthetic", "fake", "decraft", "generated", "dummy"))
    assert "<title>Acme Power Ltd</title>" in html
    assert "Income statement (Rs cr)" not in html and "<h3>Table 1</h3>" in html   # E-36: no source caption
    assert html.index("<th>Line item</th>") < html.index("<th>FY25</th>")    # source column order
    assert html.index("Regional review") < html.index("<th>Region</th>")     # tables sit in their section
    assert "Page 1 - Acme Content" not in html


def test_cells_are_formatted_like_the_source():
    assert format_number(-1234.5, 1, {"brackets": True}) == "(1,234.5)"
    assert format_number(12.3, 1, {"percent": True}) == "12.3%"
    assert format_number(None, 0, {"blank": "-"}) == "-"


# ---------------------------------------------------------------- PDF file metadata
def test_pdf_metadata_looks_like_an_ordinary_document_with_no_synthetic_marker(built, tmp_path):
    """E-32 (task 9): nothing in the PDF - pages, HTML or file metadata - says it is generated."""
    import pymupdf
    doc, _, _, data = built
    narrative = NarrativeDoc(sections=[NarrativeSection(section_id="s1", title="Overview", text="Text.")],
                             word_count=1, llm_used=False)
    result = render(data, narrative, {"entity": {"entity_name": "Acme Power Ltd"}}, CFG, tmp_path, plan=PLAN, doc=doc)
    with pymupdf.open(result.pdf_path) as pdf:
        assert pdf.metadata["title"] == "Acme Power Ltd" and pdf.metadata["author"] == "Acme Power Ltd"
        assert "weasyprint" not in pdf.metadata["producer"].lower()
        info = int(pdf.xref_get_key(-1, "Info")[1].split()[0])
        everything = " ".join(str(v) for v in pdf.metadata.values()) + str(pdf.xref_object(info))             + (pdf.get_xml_metadata() or "") + "".join(page.get_text() for page in pdf)
        assert not any(w in everything.lower() for w in ("synthetic", "decraft", "generated", "fake"))
    assert result.qa["rendered_pdf_qa"]["checks"]["charts_present"]


# ---------------------------------------------------------------- headline privacy compares like with like
def test_headline_check_uses_the_same_definitions_on_both_sides(built):
    doc, report, plan, data = built
    copied = data.__class__(**{**data.__dict__})
    source_display = {}
    for t in doc.tables:
        from src.core.frames import table_frame
        frame = table_frame(t, [p for p in report.profiles if p.table_id == t.table_id])[0]
        for p in report.profiles:
            if p.table_id == t.table_id and pd.api.types.is_numeric_dtype(frame[p.column]):
                frame[p.column] = (frame[p.column] / p.scale).round(p.decimals or 0)
        source_display[t.table_id] = frame
    copied.facts = compute_facts(type("D", (), {"display": source_display, "source_profiles": report.profiles,
                                                "key_columns": data.key_columns})())
    flagged = audit_privacy(doc, copied, plan, CFG)
    assert any(v.check == "headline_distance" for v in flagged.violations)      # identical facts are caught


# ---------------------------------------------------------------- E-38 Font Lock in the current renderer
BLUEPRINT = {"entity": {"entity_name": "Acme Power Ltd"},
             "text_blocks": [{"font_name": "Cambria", "font_size": 8.04}] * 50 + [{"font_name": "Cambria-Bold",
                                                                                  "font_size": 9.0}] * 5,
             "headings": [{"level": "H1", "text": "Acme Power Ltd", "page": 1, "font_size": 16.0},
                          {"level": "H2", "text": "Mumbai Operations", "page": 1, "font_size": 12.0},
                          {"level": "H3", "text": "Segment detail", "page": 2, "font_size": 10.0}],
             "page_structure": [{"size": {"width": 595.3, "height": 841.9}, "margins": {"left": 36, "top": 32}}]}


def test_every_font_size_comes_from_the_source(built, tmp_path):
    import re
    doc, _, _, data = built
    narrative = write(data, PLAN, CFG)
    html = build_html(data, narrative, BLUEPRINT, PLAN, doc)
    sizes = dict(re.findall(r"(body|h1|h2|h3|table|figcaption) \{[^}]*?font-size: ([\d.]+)pt", html))
    assert sizes == {"body": "8", "h1": "16", "h2": "12", "h3": "10", "table": "8", "figcaption": "8"}
    result = render(data, narrative, BLUEPRINT, CFG, tmp_path, plan=PLAN, doc=doc)
    svg = (tmp_path / "charts" / "c_sales.svg").read_text(encoding="utf-8")
    found = set(re.findall(r"font-size:\s*([\d.]+)px", svg))
    assert found and found <= {"8", "8.0"}                                          # chart text at body size
    width = re.search(r'<svg[^>]*width="([\d.]+)pt"', svg).group(1)
    assert f"width: {float(width):.1f}pt" in html.replace(f"{float(width)}pt", f"{float(width):.1f}pt")         or f"{width}pt" in html                                                     # shown at drawn size


def test_source_headings_are_never_used_as_titles(built):
    doc, _, _, data = built
    narrative = write(data, PLAN, CFG)
    html = build_html(data, narrative, BLUEPRINT, PLAN, doc)
    assert "Mumbai Operations" not in html and "Segment detail" not in html
