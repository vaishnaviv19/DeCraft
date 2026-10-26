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


def fixtures(section_blocks=("p1b01",), source_words=9, facts=None, second_section=False):
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


def test_a_sentence_in_the_claim_paragraph_field_is_turned_into_its_index():
    from src.narrative._writer import locate_claims
    out = WriterOut.model_validate({
        "blocks": [{"id": "p1b01", "text": "First paragraph.\n\nThe Chief Executive confirmed the plan in FY25."}],
        "claims": [{"e": "E1", "a": "role", "v": "CEO", "p": "The Chief Executive confirmed the plan", "b": "p1b01"},
                   {"e": "E1", "a": "year", "v": "FY25", "p": "1", "b": "p1b01"},
                   {"e": "E1", "a": "x", "v": "y", "p": "not there", "b": "p1b01"}]})
    locate_claims(out)
    assert [c.p for c in out.claims] == [1, 1, 0]


def test_writer_claims_are_merged_without_a_conflict(gateway_for):
    data, outline, bible, templates, blocks = fixtures()
    payload = json.dumps({"blocks": [{"id": "p1b01",
                                      "text": "Revenue reached {{ rev|:0 }} in FY25."}],
                          "claims": [{"e": "E1", "a": "year", "v": "FY25", "p": 0, "b": "p1b01"}]})
    doc = write_blocks(data, outline, bible, templates, None, CFG, gateway_for(FakeLLM([payload])))
    assert doc.claims == 1 and doc.new_entities == 0
    assert doc.unknown_refs == 0 and doc.conflicts == 0


def test_two_writers_disagreeing_are_reconciled(gateway_for):
    data, _, _, _, _ = fixtures()
    data.allowed_periods = ["FY24", "FY25"]
    blocks = {"p1b01": Block(block_id="p1b01", kind="text", bbox=(0, 0, 200, 20),
                             text="Source one.", source_words=9),
              "p2b01": Block(block_id="p2b01", kind="text", bbox=(0, 0, 200, 20),
                             text="Source two.", source_words=9)}
    templates = [PageTemplate(page=1, mode="mirror", reason="", blocks=list(blocks.values()))]
    outline = Outline(report_title="Acme", sections=[
        SectionPlan(section_id="S1", source_title="One", title="One", goal="g",
                    block_ids=["p1b01"], fact_keys=["rev"]),
        SectionPlan(section_id="S2", source_title="Two", title="Two", goal="g",
                    block_ids=["p2b01"], fact_keys=["rev"])])
    bible = StoryBible(version=1, entities=[BibleEntity(
        id="E1", kind="initiative", name="Project Meridian", attrs={"year": "FY24"})])
    reply1 = json.dumps({"blocks": [{"id": "p1b01",
                                     "text": "Meridian launched {{ rev|:0 }} in FY24."}],
                         "claims": [{"e": "E1", "a": "year", "v": "FY24", "p": 0, "b": "p1b01"}]})
    reply2 = json.dumps({"blocks": [{"id": "p2b01",
                                     "text": "Meridian launched {{ rev|:0 }} in FY25."}],
                         "claims": [{"e": "E1", "a": "year", "v": "FY25", "p": 0, "b": "p2b01"}]})
    fix = json.dumps({"choice": "FY25", "reason": "timeline",
                      "rewrites": [{"block_id": "p1b01",
                                    "text": "Meridian launched {{ rev|:0 }} in FY25."}]})
    cfg = load_config(llm_enabled=False)
    text = cfg.providers.text.model_copy(update={"max_concurrency": 1})
    cfg = cfg.model_copy(update={"providers": cfg.providers.model_copy(update={"text": text})})
    doc = write_blocks(data, outline, bible, templates, None, cfg,
                       gateway_for(FakeLLM([reply1, reply2, fix])))
    assert doc.conflicts == 1 and doc.resolved == 1
    assert "FY24" not in doc.sections[0].text and "FY25" in doc.sections[0].text


def test_a_section_without_figures_still_fills_its_word_budget_with_varied_text():
    from src.narrative._writer import _fallback_section
    empty = SectionPlan(section_id="S9", source_title="x", title="Outlook", goal="g", block_ids=[], fact_keys=[])
    one, two = _fallback_section(empty, {}, 80, 0), _fallback_section(empty, {}, 80, 1)
    assert len(one.split()) >= 80 * 0.8 and one != two
    assert not any(ch.isdigit() for ch in one)                 # no invented figures


def test_the_retry_message_stays_inside_the_input_cap():
    from src.narrative._writer import retry_payload
    data, outline, bible, templates, blocks = fixtures()
    first, _ = build_writer_payload(outline.sections[0], outline, bible, data.facts, data.allowed_periods,
                                    10 ** 6, blocks)
    cap = estimate_tokens(first) + 150
    errors = [f"p1b01: a very long complaint number {i} " + "x" * 400 for i in range(30)]
    retry = retry_payload(outline.sections[0], outline, bible, data.facts, data.allowed_periods, cap, blocks, errors)
    assert estimate_tokens(retry) <= cap
    assert len(json.loads(retry)["err"]) <= 8


def test_a_section_over_the_cap_falls_back_instead_of_stopping_the_stage(gateway_for):
    from src.errors import LLMBudgetExceeded
    data, outline, bible, templates, blocks = fixtures()

    class OverCap:
        enabled = True

        def ask_json(self, *args, **kwargs):
            raise LLMBudgetExceeded("section_writer: 3666 input tokens > cap 3200")

    doc = write_blocks(data, outline, bible, templates, None, CFG, OverCap())
    assert doc.sections and doc.sections[0].text and not doc.llm_used


def _two_sections(words=9):
    data, _, _, _, _ = fixtures()
    blocks = {"p1b01": Block(block_id="p1b01", kind="text", bbox=(0, 0, 200, 20), text="Source one.",
                             source_words=words),
              "p2b01": Block(block_id="p2b01", kind="text", bbox=(0, 0, 200, 20), text="Source two.",
                             source_words=words)}
    templates = [PageTemplate(page=1, mode="mirror", reason="", blocks=list(blocks.values()))]
    outline = Outline(report_title="Acme", sections=[
        SectionPlan(section_id="S1", source_title="One", title="One", goal="g", block_ids=["p1b01"],
                    fact_keys=["rev"]),
        SectionPlan(section_id="S2", source_title="Two", title="Two", goal="g", block_ids=["p2b01"],
                    fact_keys=["rev"])])
    bible = StoryBible(version=1, entities=[])
    return data, outline, bible, templates


SAME = "Demand stayed firm across every region during the period."


def test_a_repeated_sentence_is_rewritten_once_with_rep(gateway_for):
    """v3.3 F-11: the later section is retried with the repeated sentence listed in 'rep'."""
    data, outline, bible, templates = _two_sections()
    first = json.dumps({"blocks": [{"id": "p1b01", "text": SAME}]})
    second = json.dumps({"blocks": [{"id": "p2b01", "text": SAME}]})
    fixed = json.dumps({"blocks": [{"id": "p2b01", "text": "Costs were held below the agreed budget lines."}]})
    llm = FakeLLM([first, second, fixed])
    data.facts = {"rev": data.facts["rev"]}
    cfg = CFG.model_copy(update={"providers": CFG.providers.model_copy(
        update={"text": CFG.providers.text.model_copy(update={"max_concurrency": 1})})})
    doc = write_blocks(data, outline, bible, templates, None, cfg, gateway_for(llm))
    assert doc.repeats["found"] == 1 and doc.repeats["fixed"] == 1
    assert '"rep"' in llm.calls[-1][1] and SAME not in doc.blocks["p2b01"]


def test_a_repeat_without_a_model_is_dropped_or_flagged():
    from src.narrative._repeats import cross_section_repeats, drop_sentence
    text = SAME + " Costs were held below the agreed budget lines for the year."
    assert cross_section_repeats({"S1": SAME, "S2": text})
    assert drop_sentence(text, SAME) == "Costs were held below the agreed budget lines for the year."
