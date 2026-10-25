# src/generation/_keys.py
"""Fresh Keys (M5.1): generated identifiers in a format and range disjoint from the source."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

KEY_RE = re.compile(r"^(?P<prefix>[A-Za-z]{1,6})(?P<sep>[-_/ ]?)(?P<num>[0-9]+)$")


def _parts(values: list[str]):
    matches = [KEY_RE.match(v) for v in values]
    matches = [m for m in matches if m]
    if not matches:
        return None
    width = max(len(m.group("num")) for m in matches)
    nums = [int(m.group("num")) for m in matches]
    return matches[0].group("prefix"), matches[0].group("sep"), width, nums


def fresh_key_values(source: pd.Series, n: int, rng: np.random.Generator,
                     min_gap: int = 7) -> list:
    """`n` new keys that are never equal to a source key. Numeric keys stay numeric."""
    values = [str(v).strip() for v in source.dropna()]
    numeric = pd.api.types.is_numeric_dtype(source.dropna())
    parts = _parts(values)
    if parts is not None and not numeric:
        prefix, sep, width, nums = parts
        start = (max(nums) if nums else 0) + int(rng.integers(min_gap, 100))
        out = [f"{prefix}{sep}{start + i:0{width}d}" for i in range(n)]
    else:
        start = int(rng.integers(900_000, 999_999))
        out = [str(start + i) for i in range(n)]
    taken = set(values)
    while any(v in taken for v in out):
        start += n
        out = [f"{prefix}{sep}{start + i:0{width}d}" for i in range(n)] if parts and not numeric \
            else [str(int(start) + i) for i in range(n)]
    return out
