# src/core/units.py
"""Units and Scale (M1.3). Values are stored in Base Units; Scale is used only to parse and format.

A "thousands" header means the displayed number is 1,000 times the base value, so a scale of 1e3 is
multiplied in on read and divided out on display. Percentages and counts are never scaled.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

WORD_MULT = {"thousand": 1e3, "thousands": 1e3, "k": 1e3, "000": 1e3, "000s": 1e3,
             "million": 1e6, "millions": 1e6, "mn": 1e6, "mm": 1e6, "m": 1e6,
             "billion": 1e9, "billions": 1e9, "bn": 1e9, "b": 1e9,
             "lakh": 1e5, "lakhs": 1e5, "crore": 1e7, "crores": 1e7, "cr": 1e7}

# Longest alternatives first so "millions" is not read as "m". A scale word must not be part of a
# longer word ("week" does not contain a scale suffix). A digit, symbol or bracket may precede it,
# as in "$ millions", "in $bn", "(Rs. cr)" and "Revenue of $1.25 billion".
SCALE_RE = re.compile(
    r"(?<![a-z])(thousands?|millions?|billions?|lakhs?|crores?|000s?|mn|mm|bn|cr|m|k|b)(?![a-z])",
    re.I,
)


@dataclass(frozen=True)
class ScaleInfo:
    multiplier: float = 1.0
    evidence: str = "none"


def parse_scale(text: str) -> ScaleInfo:
    m = SCALE_RE.search(text or "")
    if not m:
        return ScaleInfo()
    return ScaleInfo(WORD_MULT[m.group(1).lower()], m.group(0).strip())


def resolve_scale(sources: list[str]) -> tuple[ScaleInfo, list[str]]:
    """`sources` are ordered by priority: header, unit row, caption, nearby text.

    The first stated scale wins; a later source that states a different one produces a warning.
    """
    found = [(s, parse_scale(s)) for s in sources]
    found = [(s, i) for s, i in found if i.multiplier != 1.0]
    if not found:
        return ScaleInfo(), []
    best = found[0][1]
    warns = [f"scale conflict: {i.evidence!r} contradicts {best.evidence!r}"
             for _, i in found[1:] if i.multiplier != best.multiplier]
    return best, warns


def to_base(value: float, scale: ScaleInfo) -> float:
    return value * scale.multiplier


def from_base(value: float, scale: ScaleInfo) -> float:
    return value / scale.multiplier


# A scale stated in running text is trusted only when a currency marker introduces it ("Rs. cr",
# "in $ millions"). Prose that happens to contain "m" is not a scale statement.
CONTEXT_SCALE_RE = re.compile(
    r"(?:rs\.?|inr|usd|eur|gbp|[$€£₹])\s*(?:[0-9][0-9.,]*\s*)?"
    r"(thousands?|millions?|billions?|lakhs?|crores?|000s?|mn|mm|bn|cr|m|k|b)(?![a-z])",
    re.I,
)
CURRENCY_CODE_RE = re.compile(r"\b(inr|usd|eur|gbp)\b|rs\.?|[$€£₹]", re.I)
CURRENCY_CODES = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "rs": "INR", "rs.": "INR",
                  "inr": "INR", "usd": "USD", "eur": "EUR", "gbp": "GBP"}


def parse_context_scale(text: str) -> ScaleInfo:
    m = CONTEXT_SCALE_RE.search(text or "")
    if not m:
        return ScaleInfo()
    return ScaleInfo(WORD_MULT[m.group(1).lower()], m.group(0).strip())


def currency_code(text: str) -> str | None:
    m = CURRENCY_CODE_RE.search(text or "")
    return CURRENCY_CODES.get(m.group(0).lower()) if m else None
