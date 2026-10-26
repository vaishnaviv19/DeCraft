# tests/narrative/test_plan.py
import json
from pathlib import Path

import numpy as np
import pymupdf
import pytest

from src.config import load_config
from src.contracts import SourceDocument, SourceTable
from src.contracts.dataset import empty_dataset
from src.contracts.report import SectionPlan
from src.generation import generate
from src.inference import infer
from src.narrative import plan
from src.narrative._plan import (BibleOut, EntityOut, TitleSection, TitlesOut, bible_errors,
                                 titles_are_valid)
from src.privacy import plan_privacy
from src.style import build_templates, extract_style
from tests.conftest import FakeLLM

CFG = load_config(llm_enabled=False)


def two_section_pdf(path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(72, 200, 520, 260), "First section body text about sales.",
                        fontsize=10, fontname="helv")
    page.insert_text((72, 120), "Revenue Review", fontsize=16, fontname="hebo")
    page.insert_text((72, 320), "Cost Review", fontsize=16, fontname="hebo")
    page.insert_textbox(pymupdf.Rect(72, 340, 520, 400), "Second section body text about costs.",
                        fontsize=10, fontname="helv")
    document.save(path)
    document.close()
    return path


def prepared(path: Path):
    cfg = load_config(llm_enabled=False)
    style = extract_style(path, cfg)
    templates = build_templates(path, None, style, cfg)
    rows = [[f"Item {i}", str(10 + i)] for i in range(8)]
    doc = SourceDocument(document_sha256="plan12345", entity_name="Acme Industries",
                         tables=[SourceTable(table_id="t", page=1, header=["Item", "Value"],
                                             raw_cells=rows)],
                         text_blocks=["Acme Industries reported growth at Falcon Division."])
    report = infer(doc, cfg)
    data = generate(doc, report, plan_privacy(doc, report, cfg), cfg,
                    np.random.default_rng(cfg.seed))
    return doc, data, templates


def test_skeleton_owns_every_block_and_fact_once(tmp_path):
    doc, data, templates = prepared(two_section_pdf(tmp_path / "two.pdf"))
    outline, bible = plan(data, templates, doc, CFG, None)
    assert outline.sections and bible.entities
    seen_blocks, seen_facts = [], []
    for section in outline.sections:
        assert section.title and section.goal
        seen_blocks.extend(section.block_ids)
        seen_facts.extend(section.fact_keys)
    text_blocks = [block.block_id for page in templates for block in page.blocks
                   if block.kind in {"text", "heading", "caption"}]
    assert sorted(seen_blocks) == sorted(text_blocks)
    assert len(seen_blocks) == len(set(seen_blocks))
    assert sorted(seen_facts) == sorted(data.facts) and len(seen_facts) == len(set(seen_facts))
    assert all(fact.owner != "S0" for fact in data.facts.values())


def test_titles_from_the_model_are_applied_in_order(tmp_path, gateway_for):
    doc, data, templates = prepared(two_section_pdf(tmp_path / "titles.pdf"))
    skeleton, _ = plan(data, templates, doc, CFG, None)
    reply = json.dumps({"report_title": "Acme at a Glance",
                        "sections": [{"id": section.section_id, "title": f"New Heading {i + 1}",
                                      "goal": f"Goal {i + 1}"}
                                     for i, section in enumerate(skeleton.sections)]})
    outline, _ = plan(data, templates, doc, CFG, gateway_for(FakeLLM([reply])))
    assert outline.report_title == "Acme at a Glance"
    assert [section.title for section in outline.sections] == [
        f"New Heading {i + 1}" for i in range(len(outline.sections))]


def test_a_reordered_or_copied_title_is_rejected(tmp_path, gateway_for):
    doc, data, templates = prepared(two_section_pdf(tmp_path / "bad_titles.pdf"))
    skeleton, _ = plan(data, templates, doc, CFG, None)
    reordered = list(skeleton.sections)
    reordered.reverse()
    reply = json.dumps({"report_title": "X",
                        "sections": [{"id": section.section_id, "title": section.source_title,
                                      "goal": "g"} for section in reordered]})
    outline, _ = plan(data, templates, doc, CFG, gateway_for(FakeLLM([reply])))
    assert [section.title for section in outline.sections] != [section.source_title for section in reordered]
    assert all(section.title for section in outline.sections)


def test_title_validation_rules():
    outline = type("O", (), {"sections": [SectionPlan(section_id="S1",
                                                      source_title="Revenue Growth Momentum Review",
                                                      title="", goal="", block_ids=[],
                                                      fact_keys=[])]})()
    good = TitlesOut(report_title="R", sections=[TitleSection(id="S1", title="Sales Momentum",
                                                              goal="g")])
    assert titles_are_valid(good, outline)
    copied = TitlesOut(report_title="R", sections=[TitleSection(id="S1",
                                                                title="Revenue Growth Momentum Review",
                                                                goal="g")])
    assert not titles_are_valid(copied, outline)
    too_long = TitlesOut(report_title="R", sections=[TitleSection(id="S1",
                                                                  title=" ".join(["word"] * 13),
                                                                  goal="g")])
    assert not titles_are_valid(too_long, outline)


def test_bible_retries_with_the_error_then_accepts_a_valid_reply(tmp_path, gateway_for):
    doc, data, templates = prepared(two_section_pdf(tmp_path / "bible.pdf"))
    data.allowed_periods = ["FY24", "FY2024", "FY25", "FY2025"]
    skeleton, _ = plan(data, templates, doc, CFG, None)
    titles = json.dumps({"report_title": "Acme in Brief",
                         "sections": [{"id": section.section_id, "title": f"Section {i + 1}",
                                       "goal": "Explain the figures."}
                                      for i, section in enumerate(skeleton.sections)]})
    bad = json.dumps({"entities": [{"id": f"E{i}", "kind": "initiative",
                                    "name": f"Init {chr(65 + i)}",
                                    "attrs": {"year": "FY27"}} for i in range(8)]})
    good = json.dumps({"entities": [{"id": f"E{i}", "kind": "initiative",
                                     "name": f"Init {chr(65 + i)}",
                                     "attrs": {"year": "FY25"}} for i in range(8)]})
    llm = FakeLLM([titles, bad, good])
    _, bible = plan(data, templates, doc, CFG, gateway_for(llm))
    assert len(llm.calls) == 3 and "FY27" in llm.calls[2][1]
    assert len(bible.entities) == 8 and bible.entities[0].attrs["year"] == "FY25"


def test_a_bible_with_too_many_entities_falls_back(tmp_path, gateway_for):
    doc, data, templates = prepared(two_section_pdf(tmp_path / "many.pdf"))
    data.allowed_periods = ["FY25"]
    reply = json.dumps({"entities": [{"id": f"E{i}", "kind": "initiative", "name": f"Init {i}",
                                      "attrs": {}} for i in range(21)]})
    _, bible = plan(data, templates, doc, CFG, gateway_for(FakeLLM([reply])))
    assert 8 <= len(bible.entities) <= 20
    assert len({entity.name for entity in bible.entities}) == len(bible.entities)


def test_bible_validation_reports_duplicates_and_bad_years():
    doc = SourceDocument(document_sha256="x", entity_name="Acme", tables=[], text_blocks=[])
    out = BibleOut(entities=[EntityOut(id="E1", kind="initiative", name="Same", attrs={"year": "FY27"}),
                             EntityOut(id="E2", kind="initiative", name="same", attrs={})])
    errors = bible_errors(out, doc, ["FY25"], set())
    assert any("8-20" in error for error in errors)
    assert any("duplicate entity names" in error for error in errors)
    assert any("FY27" in error for error in errors)


# ---------------------------------------------------------------- live run 2026-10-26: 44 sections, one fact owner
def test_table_rows_and_number_lines_are_never_headings():
    from src.style._templates import can_be_heading, numeric_share
    row = {"lines": [{"spans": [{"text": "Particulars", "bbox": (10, 100, 60, 110), "size": 8},
                                {"text": "Methodology", "bbox": (120, 100, 180, 110), "size": 8},
                                {"text": "Multiple", "bbox": (240, 100, 280, 110), "size": 8}]}]}
    title = {"lines": [{"spans": [{"text": "Margins expand on lower input cost", "bbox": (10, 50, 300, 66), "size": 16}]}]}
    assert numeric_share("Revenue 32,703 42,816 55,109 59,511") > 0.3
    assert numeric_share("Y.E March FY21A FY22A FY23A FY24E") > 0.3
    assert not can_be_heading(row, "Particulars Methodology Multiple")
    assert can_be_heading(title, "Margins expand on lower input cost")


def test_facts_go_to_the_section_that_shows_their_table():
    from src.contracts.report import Block, PageTemplate
    from src.narrative._plan import table_sections
    blocks = [Block(block_id="a", kind="heading", bbox=(0, 10, 100, 20), text="Overview", section_id="S1"),
              Block(block_id="b", kind="text", bbox=(0, 30, 100, 40), text="Body words here", section_id="S1"),
              Block(block_id="c", kind="heading", bbox=(0, 50, 100, 60), text="Figures", section_id="S2"),
              Block(block_id="d", kind="text", bbox=(0, 70, 100, 80), text="Revenue 100 120 140", section_id="S2")]
    doc = SourceDocument(document_sha256="own12345", tables=[SourceTable(table_id="t9", page=1, header=["A", "B"],
                                                                          raw_cells=[["x", "1"]])])
    assert table_sections([PageTemplate(page=1, mode="mirror", blocks=blocks)], doc, {"S1", "S2"}) == {"t9": "S2"}


def test_fallback_titles_come_from_the_figures_and_are_never_numbered_repeats():
    from src.narrative._plan import _fallback_title
    used: set[str] = set()
    titles = []
    for i in range(20):
        keys = [f"t.row:Revenue.latest", f"t.row:EBITDA.growth", f"t.row:Value (Rs. cr.).latest"] if i == 0 else []
        section = SectionPlan(section_id=f"S{i}", source_title="PROFIT & LOSS", title="", goal="",
                              block_ids=[], fact_keys=keys)
        title, goal = _fallback_title(section, i, used)
        used.add(title)
        titles.append(title)
    assert titles[0] == "Revenue, EBITDA and Value" and "Revenue" in _fallback_title(
        SectionPlan(section_id="x", source_title="", title="", goal="", block_ids=[],
                    fact_keys=["t.row:Revenue.latest"]), 0)[1]
    assert not any(t[-1].isdigit() for t in titles)


def test_document_type_comes_from_the_titles_call_and_reaches_the_bible(tmp_path, gateway_for):
    """E-25: 'dt' is named by the Titles call from page 1 and 'dom' is the Annotator's domain; both
    are passed on (before outline_titles v3 every payload carried an empty 'dt')."""
    doc, data, templates = prepared(two_section_pdf(tmp_path / "dt.pdf"))
    skeleton, _ = plan(data, templates, doc, CFG, None)
    reply = json.dumps({"dt": "Results update", "report_title": "Acme at a Glance",
                        "sections": [{"id": section.section_id, "title": f"New Heading {i + 1}",
                                      "goal": f"Goal {i + 1}"}
                                     for i, section in enumerate(skeleton.sections)]})
    llm = FakeLLM([reply, "{}"])
    outline, _ = plan(data, templates, doc, CFG, gateway_for(llm), domain="utilities")
    assert outline.doc_type == "results update" and outline.domain == "utilities"
    titles_payload, bible_payload = llm.calls[0][1], llm.calls[1][1]
    assert '"dom": "utilities"' in titles_payload
    assert '"dt": "results update"' in bible_payload and '"dom": "utilities"' in bible_payload
    roles = json.loads(titles_payload[titles_payload.index("{"):titles_payload.rindex("}") + 1])["secs"]
    assert any("body text" in item["role"] for item in roles)       # first paragraph, not the title


def test_a_doc_type_with_digits_or_too_many_words_is_ignored():
    from src.narrative._plan import clean_doc_type
    assert clean_doc_type("Annual Report 2024") == ""
    assert clean_doc_type("a b c d e f g") == ""
    assert clean_doc_type("Equity Research Note") == "equity research note"
