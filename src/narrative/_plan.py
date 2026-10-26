# src/narrative/_plan.py
"""Planning (M8.6): the Outline skeleton (Python), the Titles call and the Story Bible call.

The model writes titles and invented entities; Python validates both (E-11, E-25, E-26, E-28) and
falls back deterministically, so the Plan stage always completes."""
from __future__ import annotations

import json
import re
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

from src.config import Config
from src.contracts import SourceDocument
from src.contracts.dataset import CanonicalDataset
from src.contracts.report import (BibleEntity, Block, Outline, SectionPlan, StoryBible)
from src.core.originality import reused_names, source_proper_nouns
from src.llm import LLMGateway
from src.narrative._prompts import BIBLE, TITLES
from src.narrative._tokens import illegal_digits

FICTIONAL = ["Aster", "Borealis", "Cinder", "Dovetail", "Ember", "Fathom", "Gale", "Harbor",
             "Isolde", "Juniper", "Kestrel", "Lumen", "Meridian", "Nimbus", "Orion", "Pinnacle"]
GENERIC_TITLE_WORDS = {"overview", "performance", "year", "review", "summary", "report",
                       "introduction", "results", "operations", "financial", "highlights", "outlook",
                       "business", "section", "the", "and", "of", "in", "at", "a", "an", "for"}


class TitleSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    goal: str = ""


class TitlesOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_title: str = ""
    sections: list[TitleSection]


class EntityOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: str
    name: str
    attrs: dict[str, str] = Field(default_factory=dict)


class BibleOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[EntityOut]


# ---------------------------------------------------------------- skeleton
def _section_order(templates) -> list[str]:
    order: list[str] = []
    for page in templates:
        for block in sorted(page.blocks, key=lambda b: (b.bbox[1], b.bbox[0])):
            section = block.section_id or "S0"
            if section not in order:
                order.append(section)
    return order


def build_skeleton(data: CanonicalDataset, templates, doc: SourceDocument) -> Outline:
    order = _section_order(templates)
    headings: dict[str, str] = {}
    first_text: dict[str, str] = {}
    block_ids: dict[str, list[str]] = {section: [] for section in order}
    for page in templates:
        for block in page.blocks:
            section = block.section_id or "S0"
            if block.kind == "heading" and section not in headings and block.text:
                headings[section] = block.text.strip()
            if block.kind in {"text", "heading", "caption"}:
                block_ids.setdefault(section, []).append(block.block_id)
                if block.kind == "text" and section not in first_text and block.text:
                    first_text[section] = block.text.strip()
    if not order:
        order = ["S1"]
    facts_by_section: dict[str, list[str]] = {section: [] for section in order}
    default_owner = next((section for section in order if section != "S0"), order[0])
    table_owner = table_sections(templates, doc, set(order) - {"S0"})
    for key, fact in data.facts.items():
        # a fact belongs to the section that shows its table (live run 2026-10-26: every fact went to S1)
        owner = table_owner.get(key.split(".", 1)[0], default_owner)
        fact.owner = owner
        facts_by_section[owner].append(key)
    return Outline(
        report_title=doc.entity_name or (doc.title or "Report"),
        sections=[SectionPlan(section_id=section, source_title=headings.get(section, "Front matter"),
                              title="", goal="", block_ids=block_ids.get(section, []),
                              fact_keys=facts_by_section[section]) for section in order])


def table_sections(templates, doc: SourceDocument | None, sections: set[str]) -> dict[str, str]:
    """Source table id -> the section that shows it. On each page the tables (in source order) are
    matched, in reading order, to the page's table blocks, or - for borderless tables - to the runs of
    number-heavy text blocks; a page without either gives its tables to the section with most blocks."""
    from src.style import numeric_share
    out: dict[str, str] = {}
    if doc is None:
        return out
    by_page: dict[int, list[str]] = {}
    for table in doc.tables:
        by_page.setdefault(int(table.page), []).append(table.table_id)
    pages = {page.page: page for page in templates}
    for number, table_ids in by_page.items():
        page = pages.get(number)
        if page is None:
            continue
        blocks = sorted((b for b in page.blocks if b.section_id in sections),
                        key=lambda b: (b.bbox[1], b.bbox[0]))
        spots = [b.section_id for b in blocks
                 if b.kind == "table" or (b.kind == "text" and numeric_share(b.text or "") > 0.3)]
        runs = [s for i, s in enumerate(spots) if i == 0 or spots[i - 1] != s]   # one spot per run
        if not runs:
            counts = Counter(b.section_id for b in blocks)
            runs = [counts.most_common(1)[0][0]] if counts else []
        for i, table_id in enumerate(table_ids):
            if runs:
                out[table_id] = runs[min(i, len(runs) - 1)]
    return out


# ---------------------------------------------------------------- titles
def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower())]


def _shared_run(source: str, title: str, size: int = 3) -> bool:
    def runs(text: str) -> set[str]:
        words = [w for w in _words(text) if w not in GENERIC_TITLE_WORDS]
        return {" ".join(words[i:i + size]) for i in range(max(0, len(words) - size + 1))}
    return bool(runs(source) & runs(title))


def titles_are_valid(reply: TitlesOut, outline: Outline) -> bool:
    wanted = [section.section_id for section in outline.sections]
    got = [section.id for section in reply.sections]
    if got != wanted:
        return False
    for section, source in zip(reply.sections, outline.sections):
        if not section.title.strip() or len(section.title.split()) > 12:
            return False
        if _shared_run(source.source_title, section.title):
            return False
    return True


FACT_LABEL_RE = re.compile(r"^[^.]+\.(?:row:)?(.+)\.(?:latest|growth|total|average|largest|largest_share|"
                           r"smallest|direction|first|last|count|share)$")
SPARE_TITLES = ["Overview", "Highlights", "Context", "Developments", "Review", "Outlook", "Background",
                "Key points", "Summary", "Perspective", "Focus areas", "Notes"]


def _section_labels(section: SectionPlan, limit: int = 3) -> list[str]:
    """The most cited line items or columns of a section's facts ('Revenue', 'EBITDA')."""
    counts: Counter = Counter()
    for key in section.fact_keys:
        if (m := FACT_LABEL_RE.match(key)) and not re.search(r"\d{3,}", m.group(1)):
            label = re.sub(r"\s*\([^)]*\)", "", m.group(1)).strip(" -*")      # 'Value (Rs. cr.)' -> 'Value'
            if label:
                counts[label] += 1
    return [label for label, _ in counts.most_common(limit)]


def _join(labels: list[str]) -> str:
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def _fallback_title(section: SectionPlan, index: int, used: set[str] | None = None) -> tuple[str, str]:
    """A title from the section's own figures ('Revenue, EBITDA and EBIT'), else a domain-free word that
    is not used yet. Never a source heading (E-36) and never a numbered repeat ('Review 2', which read
    as generated in the live run of 2026-10-26)."""
    used = used if used is not None else set()
    labels = _section_labels(section)
    if labels:
        title = _join(labels)
        if len(title.split()) <= 12 and not _shared_run(section.source_title, title) and title not in used:
            return title, f"Explain how {_join(labels)} moved and why it matters."
    spare = [t for t in SPARE_TITLES if t not in used] or SPARE_TITLES
    title = spare[index % len(spare)] if spare is SPARE_TITLES else spare[0]
    return title, "Describe the developments this part of the document covers."


def _fill_titles(outline: Outline, doc: SourceDocument, cfg: Config,
                 gateway: LLMGateway | None) -> Outline:
    if gateway is not None and gateway.enabled:
        # E-25: no fixed document type; the model sees the opening words of page 1 instead
        opening = " ".join(" ".join(doc.text_blocks).split()[:60])
        payload = {"company": doc.entity_name or "", "dom": "", "dt": "", "p1": opening,
                   "secs": [{"id": section.section_id, "src": section.source_title,
                             "role": " ".join((section.source_title or "").split()[:12]),
                             "w": len(section.block_ids) * 40}
                            for section in outline.sections],
                   "facts": [key for section in outline.sections for key in section.fact_keys][:30]}
        reply = gateway.ask_json(TITLES, json.dumps(payload), TitlesOut)
        if reply is not None and titles_are_valid(reply, outline):
            outline.report_title = reply.report_title or outline.report_title
            by_id = {section.id: section for section in reply.sections}
            for section in outline.sections:
                section.title = by_id[section.section_id].title.strip()
                section.goal = by_id[section.section_id].goal.strip()
    used: set[str] = {section.title for section in outline.sections if section.title}
    for index, section in enumerate(outline.sections):
        if not section.title:
            section.title, section.goal = _fallback_title(section, index, used)
            used.add(section.title)
    return outline


# ---------------------------------------------------------------- bible
def _period_set(data: CanonicalDataset) -> list[str]:
    periods = set(data.allowed_periods)
    if not periods:
        from src.core.periods import parse_period
        for frame in data.display.values():
            for column in frame.columns:
                if parse_period(str(column)) is not None:
                    periods.add(str(column))
    return sorted(periods)


def _normalised_periods(periods: list[str]) -> set[str]:
    allowed = set(periods)
    for token in periods:
        match = re.fullmatch(r"FY(\d{2,4})[A-Z]?", str(token), re.I)
        if match:
            year = match.group(1)
            allowed.add(f"FY20{year}" if len(year) == 2 else f"FY{year}")
            allowed.add(f"FY{int(year) % 100:02d}" if len(year) == 4 else f"FY{year}")
    return allowed


def bible_errors(out: BibleOut, doc: SourceDocument, periods: list[str], avoid: set[str]) -> list[str]:
    errors = []
    if not 8 <= len(out.entities) <= 20:
        errors.append(f"expected 8-20 entities, got {len(out.entities)}")
    ids = [entity.id for entity in out.entities]
    names = [entity.name.strip().lower() for entity in out.entities]
    if len(set(ids)) != len(ids):
        errors.append("duplicate entity ids")
    if len(set(names)) != len(names):
        errors.append("duplicate entity names")
    allowed = _normalised_periods(periods)
    for entity in out.entities:
        if entity.kind not in {"product", "event", "person_role", "initiative", "place", "org_unit",
                               "award", "partner"}:
            errors.append(f"{entity.id}: unknown kind {entity.kind!r}")
        text = " ".join([entity.name, *entity.attrs.values()])
        if illegal_digits(text, allowed):
            errors.append(f"{entity.id}: digits not in the allowed periods: {illegal_digits(text, allowed)}")
        if reused_names(text, avoid):
            errors.append(f"{entity.id}: reuses a source name {reused_names(text, avoid)}")
    return errors


def _fallback_bible(periods: list[str]) -> StoryBible:
    year = periods[-1] if periods else None
    entities = [
        BibleEntity(id="E1", kind="person_role", name="Chief Executive",
                    attrs={"holder": "Aster Lane", **({"year": year} if year else {})}),
        BibleEntity(id="E2", kind="initiative", name="Project Meridian",
                    attrs=({"year": year} if year else {})),
        BibleEntity(id="E3", kind="initiative", name="Orion Efficiency Programme",
                    attrs=({"year": year} if year else {})),
        BibleEntity(id="E4", kind="initiative", name="Harbor Digital Rollout",
                    attrs=({"year": year} if year else {})),
        BibleEntity(id="E5", kind="product", name="Nimbus Platform", attrs={}),
        BibleEntity(id="E6", kind="product", name="Kestrel Service Line", attrs={}),
        BibleEntity(id="E7", kind="place", name="Meridian Campus", attrs={}),
        BibleEntity(id="E8", kind="org_unit", name="Operations Division", attrs={}),
        BibleEntity(id="E9", kind="partner", name="Lumen Supply Partners", attrs={}),
    ]
    return StoryBible(version=1, entities=entities)


def _fill_bible(outline: Outline, data: CanonicalDataset, doc: SourceDocument, cfg: Config,
                gateway: LLMGateway | None) -> StoryBible:
    periods = _period_set(data)
    text = " ".join(doc.text_blocks)
    avoid = source_proper_nouns(text, keep={doc.entity_name} if doc.entity_name else set())
    if gateway is not None and gateway.enabled:
        errors: list[str] = []
        for _ in range(2):
            payload = {"company": doc.entity_name or "", "dom": "",
                       "outline": [{"title": section.title, "goal": section.goal}
                                   for section in outline.sections],
                       "per": periods, "avoid": sorted(avoid)[:50], "errors": errors}
            reply = gateway.ask_json(BIBLE, json.dumps(payload), BibleOut)
            if reply is None:
                break
            errors = bible_errors(reply, doc, periods, avoid)
            if not errors:
                return StoryBible(version=1, entities=[
                    BibleEntity(id=entity.id, kind=entity.kind, name=entity.name.strip(),
                                attrs={key: str(value) for key, value in entity.attrs.items()})
                    for entity in reply.entities])
    return _fallback_bible(periods)


def plan(data: CanonicalDataset, templates, doc: SourceDocument, cfg: Config,
         gateway: LLMGateway | None) -> tuple[Outline, StoryBible]:
    """Outline skeleton, Titles call and Story Bible, validated in Python with deterministic fallbacks."""
    outline = build_skeleton(data, templates, doc)
    outline = _fill_titles(outline, doc, cfg, gateway)
    bible = _fill_bible(outline, data, doc, cfg, gateway)
    return outline, bible
