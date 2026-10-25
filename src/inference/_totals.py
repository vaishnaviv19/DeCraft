# src/inference/_totals.py
"""LLM-off fallback for formula discovery (decision D-05b). The only hardcoded search: a numeric column
whose header says 'total' and that equals the row-wise sum of the other additive numeric columns.
Everything else stays independent when the LLM is disabled. Also detects Total Rows (M2.5)."""
from __future__ import annotations

import re

import pandas as pd

from src.core.hierarchy import TOTAL_RE, RowMeta, link_totals, verify_links

TOTAL_HDR = re.compile(r"(^|[^a-z])(sub)?total([^a-z]|$)", re.I)
TOTAL_EXACT_RE = re.compile(r"^(sub[ -]?)?total$", re.I)


def detect_total_columns(df: pd.DataFrame, exclude: set[str] = frozenset()) -> list[str]:
    """Return formula texts such as '[Total] = sum([Q1], [Q2], [Q3])' (verified later like any proposal)."""
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and c not in exclude]
    out = []
    for tc in [c for c in numeric if TOTAL_HDR.search(str(c))]:
        parts = [c for c in numeric if c != tc and not TOTAL_HDR.search(str(c)) and "%" not in str(c)]
        if len(parts) >= 2:
            out.append(f"[{tc}] = sum({', '.join(f'[{p}]' for p in parts)})")
    return out


def total_row_links(df: pd.DataFrame, label_col: str | None) -> dict[int, list[int]]:
    """Total Row index -> indexes of the leaf rows above it at its level or deeper.

    Only a row labelled exactly 'Total'/'Subtotal' counts. Without indent levels an aggregate such as
    'Total Assets' or 'Total asset T.O' cannot be linked safely, so it is left alone.
    """
    if not label_col or label_col not in df.columns:
        return {}
    numeric = df.select_dtypes("number")
    rows = [RowMeta(label=str(label), level=0,
                    has_values=bool(numeric.iloc[i].notna().any()),
                    is_total=bool(TOTAL_EXACT_RE.match(str(label).strip().strip(":* "))))
            for i, label in enumerate(df[label_col])]
    return link_totals(rows)


def additive_columns(df: pd.DataFrame, links: dict[int, list[int]], strong: float = 0.95
                     ) -> dict[str, bool | None]:
    """Additive = the column's source Total Rows equal the sum of their children (rate >= strong)."""
    out: dict[str, bool | None] = {}
    for col in df.select_dtypes("number").columns:
        values = [None if pd.isna(v) else float(v) for v in df[col]]
        rate, _ = verify_links(values, links)
        out[str(col)] = True if rate is not None and rate >= strong else (False if rate is not None else None)
    return out
