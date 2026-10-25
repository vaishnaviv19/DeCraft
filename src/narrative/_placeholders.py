# src/narrative/_placeholders.py
"""Placeholder resolution (M8.3). The only way a number enters the narrative."""
from __future__ import annotations

import re

DIGIT = re.compile(r"[0-9]")
PLACEHOLDER = re.compile(r"\{\{\s*([^}|]+?)\s*(?:\|([^}]+))?\}\}")


class UnknownPlaceholder(KeyError):
    """A template referenced a fact key that does not exist."""


class StrayNumber(ValueError):
    """A digit appeared in a template outside a Placeholder."""


def stray_numbers(template: str) -> list[str]:
    outside = PLACEHOLDER.sub("", template)
    return DIGIT.findall(outside)


def _format(value, spec: str | None, unit: str | None) -> str:
    if spec in {"text", "word"} or isinstance(value, str):
        return str(value)
    kind, _, decimals = (spec or "").partition(":")
    places = int(decimals) if decimals.isdigit() else (0 if isinstance(value, float) else None)
    number = float(value)
    if places is None:
        rendered = f"{number:,.0f}"
    else:
        rendered = f"{number:,.{places}f}"
    if kind == "%" or (kind == "" and unit == "%"):
        return f"{rendered}%"
    return rendered


def resolve(template: str, facts: dict) -> str:
    """Replace every Placeholder; raise on a stray digit or an unknown key."""
    if stray_numbers(template):
        raise StrayNumber("template contains a digit outside a placeholder")
    out, position = [], 0
    for match in PLACEHOLDER.finditer(template):
        out.append(template[position:match.start()])
        key, spec = match.group(1).strip(), (match.group(2) or "").strip() or None
        fact = facts.get(key)
        if fact is None:
            raise UnknownPlaceholder(key)
        out.append(_format(fact.value, spec, fact.unit))
        position = match.end()
    out.append(template[position:])
    return "".join(out)


def contains_placeholder(text: str) -> bool:
    return "{{" in text or "}}" in text


def placeholder_keys(template: str) -> list[str]:
    return [m.group(1) for m in PLACEHOLDER.finditer(template)]
