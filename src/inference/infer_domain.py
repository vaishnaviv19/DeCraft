# src/inference/infer_domain.py
from __future__ import annotations

from typing import Any, Dict, List


def infer_domain(headings: List[Dict[str, Any]], text_blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    all_text = " ".join([str(h.get("text", "")) for h in headings]) + " "
    all_text += " ".join([str(b.get("text", "")) for b in text_blocks[:500]])
    t = all_text.lower()
    evidence = []
    if "power" in t or "energy" in t or "tata power" in t:
        evidence.append({"source": "header", "detail": "energy keywords", "weight": 0.7})
        return {"label": "energy", "confidence": 0.8, "evidence": evidence}
    if "bank" in t or "financial" in t:
        evidence.append({"source": "header", "detail": "finance keywords", "weight": 0.7})
        return {"label": "finance", "confidence": 0.8, "evidence": evidence}
    evidence.append({"source": "header", "detail": "no strong keywords", "weight": 0.3})
    return {"label": "unknown", "confidence": 0.4, "evidence": evidence}
