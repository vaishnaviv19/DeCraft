# src/core/values.py
"""Parsing of displayed cell strings into numbers and dates (Shared Kernel).

A Raw Cell keeps its original text; these helpers only produce the typed value used beside it.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from src.core.missingness import token_kind

UNIT_NOISE_RE = re.compile(r"\b(?:rs|inr|usd|eur|gbp)\b\.?|[$€£₹]|bps", re.I)
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d",
                "%d-%b-%y", "%d-%b-%Y", "%b-%y", "%b %Y", "%B %Y")


def parse_number(value) -> float | None:
    """Parse a displayed cell into a number; None when it is not one. Missing tokens are not numbers."""
    if token_kind(value) is not None:
        return None
    s = str(value).strip()
    neg = s.startswith("(") and s.endswith(")")
    core = s[1:-1] if neg else s
    core = UNIT_NOISE_RE.sub("", core).replace(",", "").replace("%", "").strip()
    core = core.rstrip("xX")
    if core in {"", "-", "—", "–"}:
        return None
    try:
        val = float(core)
    except ValueError:
        return None
    return -val if neg else val


def displayed_decimals(value) -> int | None:
    s = UNIT_NOISE_RE.sub("", str(value)).replace(",", "").replace("%", "").strip().rstrip("xX")
    m = re.search(r"\.([0-9]+)", s)
    return len(m.group(1)) if m else 0


def parse_date(value) -> date | None:
    s = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


NUMBER_IN_TEXT = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


INDIAN_RE = re.compile(r"\d,\d\d,\d{3}")


def is_indian_grouping(text: str) -> bool:
    """'4,28,157' / '10,95,031': lakh-crore grouping (2 digits per group after the last 3)."""
    return bool(INDIAN_RE.search(str(text)))


def group_digits(number: str, indian: bool = False) -> str:
    """'428157.5' -> '428,157.5', or '4,28,157.5' in Indian (lakh-crore) grouping."""
    whole, dot, frac = number.partition(".")
    if not indian or len(whole) <= 3:
        return f"{int(whole):,}" + (dot + frac if dot else "")
    head, last = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [last]) + (dot + frac if dot else "")


def format_number_like(template: str, value: float) -> str:
    """`value` written the way `template` writes its number: same prefix and suffix ('Rs. ', '%', 'x'),
    thousands separators, decimals and bracket negatives. 'Rs. 86,856' + 91234.4 -> 'Rs. 91,234'."""
    text = str(template).strip()
    bracket = text.startswith("(") and text.endswith(")")            # the source writes negatives as (x)
    inner = text[1:-1] if bracket else text
    m = NUMBER_IN_TEXT.search(inner)
    if m is None:
        return text
    digits = m.group(0).lstrip("+-")
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    if "," in digits:
        body = group_digits(f"{abs(value):.{decimals}f}", indian=is_indian_grouping(digits))
    else:
        body = f"{abs(value):.{decimals}f}"
    if value < 0 and bracket:
        return "(" + inner[:m.start()] + body + inner[m.end():] + ")"
    sign = "-" if value < 0 else ("+" if m.group(0).startswith("+") else "")
    return inner[:m.start()] + sign + body + inner[m.end():]

