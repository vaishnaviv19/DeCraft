# src/core/hierarchy.py
"""Total rows and their children (M2.5; the dash-means-zero check in M1.5 needs verify_links)."""
from __future__ import annotations

import re
from dataclasses import dataclass

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
