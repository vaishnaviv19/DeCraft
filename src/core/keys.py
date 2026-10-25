# src/core/keys.py
"""Fresh Keys (M5.1): generated identifiers in the SAME format as the source, never equal to one.

The format is read from the values themselves: every run of digits becomes a slot, and the LAST run
is the sequence number. 'INV-2024-001' -> 'INV-2024-###', so new keys are 'INV-2024-0xx' above the
highest source number; 'A-101' -> 'A-1xx'; 'CUST_0001' -> 'CUST_00xx'. Numeric keys stay numbers
with the same number of digits where possible. Keys with no digits at all ('Alpha', 'Beta') carry
no format to copy; the privacy plan relabels those with believable fakes (see generation._apply),
and this module only supplies a last-resort 'ID-0001' style.
"""
from __future__ import annotations

import re
from collections import Counter

import numpy as np
import pandas as pd

DIGITS_RE = re.compile(r"\d+")


def key_template(values: list[str]) -> tuple[str, str, int, int] | None:
    """-> (head, tail, width, highest sequence number) from the most common shape, or None."""
    shaped = [v for v in values if DIGITS_RE.search(v)]
    if not shaped or len(shaped) < 0.8 * len(values):
        return None
    skeleton = Counter(DIGITS_RE.sub("#", v) for v in shaped).most_common(1)[0][0]
    same = [v for v in shaped if DIGITS_RE.sub("#", v) == skeleton]
    last = [list(DIGITS_RE.finditer(v))[-1] for v in same]
    head = Counter(v[:m.start()] for v, m in zip(same, last)).most_common(1)[0][0]
    tail = Counter(v[m.end():] for v, m in zip(same, last)).most_common(1)[0][0]
    return head, tail, max(len(m.group()) for m in last), max(int(m.group()) for m in last)


def fresh_key_values(source: pd.Series, n: int, rng: np.random.Generator, min_gap: int = 7) -> list:
    """`n` new unique keys in the source format, never equal to a source key."""
    present = source.dropna()
    taken = {str(v).strip() for v in present}
    gap = int(rng.integers(min_gap, 100))
    if pd.api.types.is_numeric_dtype(present) and len(present) and (present % 1 == 0).all():
        start = int(present.max()) + gap
        out = [start + i for i in range(n)]               # numbers stay numbers, same digit count
        return [v for v in out]
    values = [str(v).strip() for v in present]
    template = key_template(values)
    if template is None:
        head, tail, width, top = "ID-", "", 4, 0
    else:
        head, tail, width, top = template
    start = top + gap
    while True:
        out = [f"{head}{start + i:0{width}d}{tail}" for i in range(n)]
        if not any(v in taken for v in out):
            return out
        start += n
