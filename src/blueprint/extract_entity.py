# src/blueprint/extract_entity.py
from __future__ import annotations

from typing import Any, Dict, List


def extract_entity(text_blocks: List[Dict[str, Any]], headings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    FR-A8: Identify the entity name, domain/industry, and tone (e.g., formal annual-report voice) from content.
    """
    all_text = " ".join([str(b.get("text", "")) for b in text_blocks[:300]])
    all_text_lower = all_text.lower()

    entity_name = None
    for h in headings:
        if h.get("level") == "H1":
            entity_name = h.get("text")
            break
    if not entity_name and text_blocks:
        # fallback to first non-trivial span
        for b in text_blocks[:50]:
            t = str(b.get("text", "")).strip()
            if len(t) > 3 and len(t) < 80:
                entity_name = t
                break

    domain = "unknown"
    if any(k in all_text_lower for k in ["power", "energy", "electricity", "tata power"]):
        domain = "energy/power"
    elif any(k in all_text_lower for k in ["bank", "financial", "equity", "mutual fund"]):
        domain = "financial services"
    elif any(k in all_text_lower for k in ["pharma", "healthcare", "biotech"]):
        domain = "healthcare/pharma"
    elif any(k in all_text_lower for k in ["it", "software", "technology"]):
        domain = "technology"
    else:
        domain = "general"

    tone = "formal"
    if "annual report" in all_text_lower or "annual" in all_text_lower:
        tone = "formal annual-report voice"
    elif "presentation" in all_text_lower:
        tone = "professional presentation voice"
    else:
        tone = "formal"

    return {"entity_name": entity_name, "domain_industry": domain, "tone": tone}
