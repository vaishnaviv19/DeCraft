# src/core/hierarchy.py
"""Total rows and their children (M2.5; the dash-means-zero check in M1.5 needs verify_links)."""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

TOTAL_RE = re.compile(r"(^|[ ])(sub)?total([ ]|$)", re.I)


@dataclass
class RowMeta:
    label: str
    level: int
    has_values: bool
    is_total: bool


def indent_levels(x0s: list[float], tol: float = 3.0) -> list[int]:
    uniq: list[float] = []
    for v in sorted(set(round(x, 1) for x in x0s)):
        if not uniq or v - uniq[-1] > tol:
            uniq.append(v)
    return [min(range(len(uniq)), key=lambda i: abs(uniq[i] - x)) for x in x0s]


def link_totals(rows: list[RowMeta]) -> dict[int, list[int]]:
    """total row index -> indexes of the rows it sums (uncovered rows at its level or deeper)."""
    uncovered: list[int] = []
    links: dict[int, list[int]] = {}
    for i, r in enumerate(rows):
        if r.is_total:
            kids = []
            while uncovered and rows[uncovered[-1]].level >= r.level:
                kids.append(uncovered.pop())
            links[i] = [k for k in reversed(kids) if rows[k].has_values]
        uncovered.append(i)
    return links


def verify_links(values: list[float | None], links: dict[int, list[int]], rel_tol: float = 0.005):
    """(Match Rate, testable rows); feed into classify_rule."""
    ok = n = 0
    for t, kids in links.items():
        if values[t] is None or not kids or any(values[k] is None for k in kids):
            continue
        n += 1
        ok += abs(sum(values[k] for k in kids) - values[t]) <= rel_tol * max(abs(values[t]), 1e-9)
    return (ok / n if n else None), n


TOTAL_EXACT_RE = re.compile(r"^(sub[ -]?)?total$", re.I)


def total_row_links(df, label_col: str | None) -> dict[int, list[int]]:
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


def additive_columns(df, links: dict[int, list[int]], strong: float = 0.95) -> dict[str, bool | None]:
    """Additive = the column's source Total Rows equal the sum of their children (rate >= strong)."""
    out: dict[str, bool | None] = {}
    for col in df.select_dtypes("number").columns:
        values = [None if pd.isna(v) else float(v) for v in df[col]]
        rate, _ = verify_links(values, links)
        out[str(col)] = True if rate is not None and rate >= strong else (False if rate is not None else None)
    return out
