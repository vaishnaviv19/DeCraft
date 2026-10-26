# src/narrative/_autounits.py   (v3.3 fix F-03: the renderer, not the writer, writes currency + scale + unit)
"""FAITH (2025) found scale errors ('$150' for '$150 million') to be the most common numeric failure. The
writer typed the scale word after a placeholder, so '{{x}} million' passed every check. Now:
  {{key|auto}}  -> '$4.5 billion', '12.4%', '3,200 units', '₹1,250 crore'  (all chosen by Python)
and any scale/unit word or currency symbol typed next to a placeholder is an error."""
from __future__ import annotations

import re

SCALES = {"western": [(1e12, "trillion"), (1e9, "billion"), (1e6, "million")],   # below 1m: full digits
          "western_short": [(1e9, "bn"), (1e6, "m")],
          "indian": [(1e7, "crore"), (1e5, "lakh")]}
SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "JPY": "¥"}
CODE = {symbol: code for code, symbol in SYMBOL.items()} | {"Rs": "INR", "Rs.": "INR"}
UNIT_WORDS = (r"(?:trillion|billion|million|thousand|bn|mn|m|k|crores?|cr|lakhs?|percent|per cent|%|"
              r"usd|eur|gbp|inr|dollars?|euros?|rupees?|pounds?)")
AFTER_RE = re.compile(r"[}][}]\s*" + UNIT_WORDS + r"(?![a-z])", re.I)
BEFORE_RE = re.compile(r"[$€£₹¥]\s*[{][{]|(?:usd|eur|gbp|inr|rs\.?)\s*[{][{]", re.I)


def format_auto(value: float, unit: str | None, style: str = "western", decimals: int = 1) -> str:
    """`value` in BASE units (a crore figure times 1e7) written with its currency, scale word and unit."""
    if unit == "%":
        return f"{value:,.{decimals}f}%"
    code = CODE.get(unit or "", unit)
    sym = SYMBOL.get(code or "", "")
    plain_unit = None if sym or not unit else unit
    a = abs(value)
    for mult, word in SCALES.get(style, SCALES["western"]):
        if a >= mult:
            num = f"{value / mult:,.{decimals}f}".rstrip("0").rstrip(".")
            sep = "" if style == "western_short" else " "
            if sym:
                return f"{sym}{num}{sep}{word}"
            return f"{num}{sep}{word}" + (f" {plain_unit}" if plain_unit else "")
    num = f"{value:,.0f}" if float(value).is_integer() else f"{value:,.{decimals}f}"
    return f"{sym}{num}" if sym else (f"{num} {plain_unit}" if plain_unit else num)


def unit_word_violations(template: str) -> list[str]:
    """Scale/unit words typed right after a placeholder, or a currency symbol/code right before one."""
    return [m.group(0) for m in AFTER_RE.finditer(template)] + [m.group(0) for m in BEFORE_RE.finditer(template)]


def detect_scale_style(source_text: str) -> str:
    """The document's own convention, so output reads like the source ('crore' vs 'million', 'bn' vs 'billion')."""
    t = (source_text or "").lower()
    counts = {"indian": len(re.findall(r"\b(?:crore|lakh)s?\b|\d\s?cr\b|\brs\.?\s?cr\b", t)),
              "western_short": len(re.findall(r"\d\s?(?:bn|mn)\b", t)),
              "western": len(re.findall(r"\b(?:billion|million|thousand)\b", t))}
    return max(counts, key=counts.get) if any(counts.values()) else "western"
