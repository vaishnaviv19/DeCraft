# src/narrative/__init__.py  -- PUBLIC INTERFACE of the narrative module (deep module)
"""Write every section as a Placeholder template (LLM) or deterministic text, then resolve it from the
Fact Store. A draft with a stray digit, an unknown key or a rose/fell contradiction is rejected; the
fallback always completes."""
from __future__ import annotations

from src.config import Config
from src.contracts import NarrativeDoc, NarrativeSection
from src.contracts.dataset import CanonicalDataset
from src.llm import LLMGateway
from src.narrative._placeholders import StrayNumber, UnknownPlaceholder, resolve
from src.narrative._templates import section_template
from src.narrative._writer import write_section

__all__ = ["write"]


def write(data: CanonicalDataset, plan, cfg: Config,
          gateway: LLMGateway | None = None) -> NarrativeDoc:
    sections: list[NarrativeSection] = []
    words = 0
    used_llm = False
    facts = data.facts
    for index, section in enumerate(plan.get("sections", []) if hasattr(plan, "get") else []):
        template = None
        if cfg.llm_enabled and gateway is not None and gateway.enabled:
            template = write_section(section, list(facts), cfg, gateway, facts)
            used_llm = used_llm or template is not None
        if template is None:
            template = section_template(section, facts, index)
        try:
            text = resolve(template, facts)
        except (StrayNumber, UnknownPlaceholder):        # never expected for the deterministic text
            text = resolve(section_template({**section, "tables": []}, {}, index), {})
        sections.append(NarrativeSection(section_id=str(section.get("section_id", "")),
                                         title=str(section.get("title", "")), text=text))
        words += len(text.split())
    return NarrativeDoc(sections=sections, word_count=words, llm_used=used_llm)
