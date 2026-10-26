# src/narrative/_placeholders.py
"""Placeholder resolution (M8.3). The only way a number enters the narrative.

`{{ key }}` uses the fact's own displayed decimals and unit (%, currency), so the text shows exactly
what the table shows. Optional format: `{{ key|%:1 }}` (percent, 1 decimal), `{{ key|:0 }}`,
`{{ key|text }}`, `{{ key|word }}`, and the flag `abs` (`{{ key|%:1|abs }}`) for "fell by 5%".
Period labels such as FY24, Q3 or H1 may be written as words; any other digit outside a placeholder
is rejected."""
from __future__ import annotations

import re

DIGIT = re.compile(r"[0-9]")
PLACEHOLDER = re.compile(r"\{\{\s*([^}|]+?)\s*(?:\|([^}]+))?\}\}")
PERIOD_WORD = re.compile(r"\b(?:(?:Q[1-4]|H[12]|9M)\s?(?:FY|CY)?\s?'?\d{0,4}|(?:FY|CY)\s?'?\d{2,4})[AEP]?\b", re.I)
CURRENCY_SYMBOLS = {"$", "₹", "€", "£"}


class UnknownPlaceholder(KeyError):
    """A template referenced a fact key that does not exist."""


class StrayNumber(ValueError):
    """A digit appeared in a template outside a Placeholder."""


class TypedUnit(ValueError):
    """A scale/unit word or currency symbol was typed next to a Placeholder (v3.3 F-03: |auto writes them)."""


def stray_numbers(template: str) -> list[str]:
    outside = PERIOD_WORD.sub("", PLACEHOLDER.sub("", template))
    return DIGIT.findall(outside)


def _format(fact, spec: str | None, style: str = "western") -> str:
    value, unit = fact.value, getattr(fact, "unit", None)
    parts = [p.strip() for p in (spec or "").split("|") if p.strip()]
    if "auto" in parts and not isinstance(value, str):
        # v3.3 F-03: currency, number, scale word and unit chosen together by Python, in the source's
        # convention; the fact is in the table's units, so it is taken back to base units first
        from src.narrative._autounits import format_auto
        number = abs(float(value)) if "abs" in parts else float(value)
        base = number * (getattr(fact, "scale", None) or 1.0)
        places = getattr(fact, "decimals", None)
        return format_auto(base, unit, style, 1 if places is None else max(int(places), 1))
    flags = {p for p in parts if p in {"abs", "text", "word"}}
    fmt = next((p for p in parts if p not in flags), "")
    if flags & {"text", "word"} or isinstance(value, str):
        return str(value)
    kind, _, decimals = fmt.partition(":")
    if decimals.isdigit():
        places = int(decimals)
    elif getattr(fact, "decimals", None) is not None:
        places = int(fact.decimals)
    else:
        places = 0 if isinstance(value, float) else None
    number = abs(float(value)) if "abs" in flags else float(value)
    rendered = f"{number:,.0f}" if places is None else f"{number:,.{places}f}"
    if kind == "%" or (kind == "" and unit == "%"):
        return f"{rendered}%"
    if unit in CURRENCY_SYMBOLS:
        return f"-{unit}{rendered[1:]}" if rendered.startswith("-") else f"{unit}{rendered}"
    return rendered


def resolve(template: str, facts: dict, style: str = "western") -> str:
    """Replace every Placeholder; raise on a stray digit, a typed unit/scale word or an unknown key.
    `style` is the source's scale convention for |auto (western, western_short, indian)."""
    if stray_numbers(template):
        raise StrayNumber("template contains a digit outside a placeholder")
    from src.narrative._autounits import unit_word_violations
    typed = unit_word_violations(template)
    if typed:
        raise TypedUnit(f"unit or scale words typed next to placeholders: {typed[:5]}")
    out, position = [], 0
    for match in PLACEHOLDER.finditer(template):
        out.append(template[position:match.start()])
        key, spec = match.group(1).strip(), (match.group(2) or "").strip() or None
        fact = facts.get(key)
        if fact is None:
            raise UnknownPlaceholder(key)
        out.append(_format(fact, spec, style))
        position = match.end()
    out.append(template[position:])
    return "".join(out)


def contains_placeholder(text: str) -> bool:
    return "{{" in text or "}}" in text


def placeholder_keys(template: str) -> list[str]:
    return [m.group(1).strip() for m in PLACEHOLDER.finditer(template)]
