# tests/narrative/test_write_blocks.py
import json

import pytest

from src.config import load_config
from src.contracts import Fact
from src.contracts.dataset import empty_dataset
from src.contracts.report import (BibleEntity, Block, Outline, PageTemplate, SectionPlan,
                                  StoryBible)
from src.llm import estimate_tokens
from src.narrative._prompts import WRITER
from src.narrative._writer import (WriterBlockOut, WriterOut, build_writer_payload, check_section,
                                   write_blocks)
from tests.conftest import FakeLLM

CFG = load_config(llm_enabled=False)


def fixtures(section_blocks=("p1b01",), source_words=5, facts=None, second_section=False):
    data = empty_dataset()
    data.allowed_periods = ["FY25", "FY2025"]
    data.facts = facts or {"rev": Fact(key="rev", value=1234.0, kind="number", unit="USD",
                                       source="t.rev:sum")}
    blocks = {block_id: Block(block_id=block_id, kind="text", bbox=(0, 0, 200, 20),
                              text="Source paragraph for overlap checks only.",
                              source_words=source_words) for block_id in section_blocks}
    templates = [PageTemplate(page=1, mode="mirror", reason="", blocks=list(blocks.values()))]
    sections = [SectionPlan(section_id="S1", source_title="Overview", title="Year in Review",
                            goal="Summarise the year.", block_ids=list(section_blocks),
                            fact_keys=["rev"])]
    if second_section:
        sections.append(SectionPlan(section_id="S2", source_title="Costs", title="Cost Review",
                                    goal="Explain costs.", block_ids=[], fact_keys=["rev"]))
    outline = Outline(report_title="Acme", sections=sections)
    bible = StoryBible(version=1, entities=[BibleEntity(id="E1", kind="initiative",
                                                        name="Project Meridian", attrs={})])
    return data, outline, bible, templates, blocks


def reply(text="Revenue reached {{ rev|:0 }} in FY25.") -> str:
    return json.dumps({"blocks": [{"id": "p1b01", "text": text}]})


def test_payload_fits_the_cap_and_drops_optional_fields_first():
    data, outline, bible, templates, blocks = fixtures(second_section=True)
    section = outline.sections[0]
    full, dropped = build_writer_payload(section, outline, bible, data.facts,
                                         data.allowed_periods, cap=10_000, blocks=blocks)
    assert dropped == []
    body = json.loads(full)
    assert {"sid", "t", "g", "bl", "f", "per"} <= set(body)
    size = estimate_tokens(full)
    tight, dropped_tight = build_writer_payload(section, outline, bible, data.facts,
                                                data.allowed_periods, cap=size - 1, blocks=blocks)
    assert dropped_tight and dropped_tight[0] == "else"
    assert estimate_tokens(tight) <= size - 1


def test_check_section_accepts_a_clean_reply_and_reports_each_error():
    data, outline, bible, templates, blocks = fixtures()
    section = outline.sections[0]
    good = WriterOut(blocks=[WriterBlockOut(id="p1b01",
                                            text="Revenue reached {{ rev|:0 }} in FY25.")])
    assert check_section(good, section, data.facts, bible, data.allowed_periods, "", blocks, CFG) == []

    missing = WriterOut(blocks=[])
    assert any("missing block ids" in error for error in
               check_section(missing, section, data.facts, bible, data.allowed_periods, "", blocks, CFG))

    stray = WriterOut(blocks=[WriterBlockOut(id="p1b01", text="Revenue grew 12% in FY25.")])
    assert any("digits" in error for error in
               check_section(stray, section, data.facts, bible, data.allowed_periods, "", blocks, CFG))

    ghost = WriterOut(blocks=[WriterBlockOut(id="p1b01", text="Revenue {{ ghost|:0 }} in FY25.")])
    assert any("unknown placeholder" in error for error in
               check_section(ghost, section, data.facts, bible, data.allowed_periods, "", blocks, CFG))

    claim = WriterOut(blocks=[WriterBlockOut(id="p1b01",
                                             text="Revenue reached {{ rev|:0 }} in FY25.")],
                      claims=[{"e": "E9", "a": "year", "v": "FY25"}])
    assert any("unknown entity" in error for error in
               check_section(claim, section, data.facts, bible, data.allowed_periods, "", blocks, CFG))

    source = "Source paragraph for overlap checks only and this text is copied verbatim from the source"
    copied = WriterOut(blocks=[WriterBlockOut(id="p1b01", text=source)])
    assert any("consecutive words" in error for error in
               check_section(copied, section, data.facts, bible, data.allowed_periods, source,
                             blocks, CFG))


def test_write_blocks_retries_with_the_errors_then_uses_the_reply(gateway_for):
    data, outline, bible, templates, blocks = fixtures()
    llm = FakeLLM([reply("Revenue grew 12% in FY25."), reply()])
    doc = write_blocks(data, outline, bible, templates, None, CFG, gateway_for(llm))
    assert doc.llm_used is True and len(llm.calls) == 2
    assert "digits" in llm.calls[1][1]
    assert "12%" not in doc.sections[0].text and "{{" not in doc.sections[0].text


def test_three_failures_fall_back_and_flag_the_section(gateway_for):
    data, outline, bible, templates, blocks = fixtures()
    bad = reply("Revenue grew 12% in FY25.")
    doc = write_blocks(data, outline, bible, templates, None, CFG, gateway_for(FakeLLM([bad] * 3)))
    assert doc.llm_used is False
    assert any("S1: fallback" in flag for flag in doc.flags)
    assert "{{" not in doc.sections[0].text and "12%" not in doc.sections[0].text


def test_without_a_gateway_the_deterministic_text_is_used():
    data, outline, bible, templates, blocks = fixtures()
    doc = write_blocks(data, outline, bible, templates, None, CFG, None)
    assert doc.llm_used is False and doc.word_count > 0
    assert all("{{" not in section.text for section in doc.sections)


# ---------------------------------------------------------------- review fixes (spec M8.7.1 / M8.7.3)
def test_every_payload_carries_tone_and_the_section_avoid_list():
    data, outline, bible, templates, blocks = fixtures()
    payload, _ = build_writer_payload(outline.sections[0], outline, bible, data.facts, data.allowed_periods,
                                      10 ** 6, blocks, tone={"person": "third"}, avoid={"Falcon X200", "Pune"})
    fields = json.loads(payload)
    assert {"dt", "dom", "tone", "avoid"} <= set(fields) and fields["tone"] == {"person": "third"}
    assert sorted(fields["avoid"]) == ["Falcon X200", "Pune"]


def test_a_draft_using_a_source_name_is_rejected():
    data, outline, bible, templates, blocks = fixtures()
    bad = WriterOut.model_validate_json(reply("The Falcon X200 lifted revenue to {{ rev|:0 }}."))
    errors = check_section(bad, outline.sections[0], data.facts, bible, data.allowed_periods, "", blocks, CFG,
                           avoid={"Falcon X200"})
    assert any("avoid" in e for e in errors)


def test_kept_table_terms_are_not_banned_from_the_text():
    from src.core.originality import drop_shown
    assert drop_shown({"Depreciation", "Current", "Mundra", "Priya Nair"},
                      ["Depreciation", "Current Liabilities", "- Aravalli UMPP"]) == {"Mundra", "Priya Nair"}


def test_fallback_text_is_built_from_each_sections_own_tables():
    import numpy as np
    from src.contracts import SourceDocument, SourceTable
    from src.generation import generate
    from src.inference import infer
    from src.narrative._writer import _fallback_section
    from src.privacy import plan_privacy
    sales = SourceTable(table_id="sales", page=1, header=["Region", "Units"],
                        raw_cells=[[r, str(u)] for r, u in zip(["North", "South", "East", "West", "Central", "Other"],
                                                               [120, 80, 150, 95, 110, 70])])
    costs = SourceTable(table_id="costs", page=1, header=["Year", "Cost"],
                        raw_cells=[[f"FY{y}", str(v)] for y, v in zip(range(19, 25), [50, 55, 61, 66, 70, 77])])
    doc = SourceDocument(document_sha256="fallbk12", tables=[sales, costs], text_blocks=["Acme review."])
    report = infer(doc, CFG)
    data = generate(doc, report, plan_privacy(doc, report, CFG), CFG, np.random.default_rng(4))
    def section(sid, table):
        return SectionPlan(section_id=sid, source_title="x", title=table, goal="g", block_ids=[],
                           fact_keys=[k for k in data.facts if k.startswith(table + ".")])
    one = _fallback_section(section("S1", "sales"), data.facts, 150, 0)
    two = _fallback_section(section("S2", "costs"), data.facts, 150, 1)
    assert "Units" in one and "Cost" not in one         # each section talks about its own table
    assert "Cost" in two and "Units" not in two
