# src/narrative/_writer.py
"""Section Writer (M8.3): one versioned LLM call per section, returning a placeholder template.

The model receives fact KEYS and labels, never values. Its draft is rejected when it writes a digit
outside a placeholder or uses a key that does not exist; the caller then uses the deterministic
template."""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from src.config import Config
from src.llm import LLMGateway, PromptSpec
from src.narrative._placeholders import stray_numbers

SECTION_PROMPT = PromptSpec(name="section_writer", version="v1", system='''You write ONE section of a data report as a template.
Return ONLY JSON: {"text": "..."} with no prose outside it.
Rules:
- Write numbers ONLY as placeholders of the form {{ key }} or {{ key|%:1 }}; never write digits yourself.
- Use only the fact keys supplied. Never invent a key or a column.
- Do not copy sentences from the source document.
- Follow the requested word count approximately.''')

MAX_DRAFT_CHARS = 6000


class SectionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


def build_prompt(section: dict, fact_keys: list[str]) -> str:
    payload = {"title": section.get("title"), "purpose": section.get("purpose"),
               "target_words": section.get("target_word_count"),
               "fact_keys": fact_keys,
               "example": "Revenue reached {{ orders.Revenue.total|:0 }} in the period."}
    return json.dumps(payload)


def write_section(section: dict, fact_keys: list[str], cfg: Config,
                  gateway: LLMGateway) -> str | None:
    """A validated placeholder template, or None so the caller falls back to the deterministic text."""
    draft = gateway.ask_json(SECTION_PROMPT, build_prompt(section, fact_keys), SectionDraft)
    if draft is None or not draft.text.strip() or len(draft.text) > MAX_DRAFT_CHARS:
        return None
    if stray_numbers(draft.text):
        return None
    if any(key not in fact_keys for key in _keys(draft.text)):
        return None
    return draft.text


def _keys(template: str) -> list[str]:
    from src.narrative._placeholders import placeholder_keys
    return placeholder_keys(template)
