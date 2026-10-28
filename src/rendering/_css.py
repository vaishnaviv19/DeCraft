# src/rendering/_css.py   (v3.3 F-10: professional typesetting details)
"""Justified text gets hyphenation in the document's language (WeasyPrint uses pyphen), so justified columns do
not show rivers of white space. Font sizes are never touched (E-29)."""
from __future__ import annotations

import re


def justify_extras(align: str) -> str:
    """The extra declarations a justified Text Style gets (appended to its CSS class)."""
    return " hyphens: auto; hyphenate-limit-chars: 6 3 3; text-align-last: left;" if align == "justify" else ""


def detect_language(text: str) -> str:
    """ISO 639-1 code of the source text (langdetect when installed); 'en' when unsure."""
    sample = " ".join((text or "").split()[:400])
    if len(sample) < 20:
        return "en"
    try:
        from langdetect import DetectorFactory, detect
        DetectorFactory.seed = 0
        code = detect(sample)
        return code if re.fullmatch(r"[a-z]{2}", code or "") else "en"
    except Exception:
        return "en"
