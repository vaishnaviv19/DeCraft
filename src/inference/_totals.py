# src/inference/_totals.py
"""LLM-off fallback for formula discovery (decision D-05b). The only hardcoded search: a numeric column
whose header says 'total' and that equals the row-wise sum of the other additive numeric columns.
Everything else stays independent when the LLM is disabled."""
from __future__ import annotations

import re

import pandas as pd

from src.core.hierarchy import additive_columns, total_row_links  # re-exported for this module's users

TOTAL_HDR = re.compile(r"(^|[^a-z])(sub)?total([^a-z]|$)", re.I)

__all__ = ["additive_columns", "detect_total_columns", "total_row_links"]


def detect_total_columns(df: pd.DataFrame, exclude: set[str] = frozenset()) -> list[str]:
    """Return formula texts such as '[Total] = sum([Q1], [Q2], [Q3])' (verified later like any proposal)."""
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in exclude]
    out = []
    for tc in [c for c in numeric if TOTAL_HDR.search(str(c))]:
        parts = [c for c in numeric if c != tc and not TOTAL_HDR.search(str(c)) and "%" not in str(c)]
        if len(parts) >= 2:
            out.append(f"[{tc}] = sum({', '.join(f'[{p}]' for p in parts)})")
    return out
