# src/narrative/_tokens.py
"""Which digits may appear in prose (decision E-10). Numbers come from placeholders. The only digits allowed
in plain text are PERIOD TOKENS (2025, FY25, FY2025, Q3 FY25, Q3 2025, H1 2025, 2024-25) and only when they
belong to the generated (shifted) period set or are a date in the Story Bible."""
import re

PLACEHOLDER_RE = re.compile(r"[{][{][^}]*[}][}]")
PERIOD_TOKEN_RE = re.compile(
    r"\b(?:(?:Q[1-4]|H[12])\s?(?:FY\s?)?\d{2,4}|FY\s?\d{2,4}(?:[-/]\d{2,4})?|(?:19|20)\d{2}(?:[-/]\d{2,4})?)\b", re.I)
DIGIT_WORD_RE = re.compile(r"\S*\d\S*")


def normalise_period(tok: str) -> str:
    t = re.sub(r"\s+", "", tok.upper())
    t = re.sub(r"FY(\d{2})(?!\d)", lambda m: f"FY20{m.group(1)}", t)
    t = re.sub(r"(Q[1-4]|H[12])(\d{2})(?!\d)", lambda m: f"{m.group(1)}20{m.group(2)}", t)
    return t


def illegal_digits(text: str, allowed_periods: set[str]) -> list[str]:
    """Digits outside placeholders that are not an allowed period token. Empty list = text is acceptable."""
    allowed = {normalise_period(p) for p in allowed_periods}
    outside = PLACEHOLDER_RE.sub(" ", text)
    bad = []
    for m in PERIOD_TOKEN_RE.finditer(outside):
        if normalise_period(m.group(0)) not in allowed:
            bad.append(m.group(0))
    rest = PERIOD_TOKEN_RE.sub(" ", outside)
    bad += [w for w in DIGIT_WORD_RE.findall(rest)]
    return bad
