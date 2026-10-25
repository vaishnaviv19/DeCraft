# src/core/rollup.py
"""Roll Up: Total Rows computed bottom-up from leaf rows (M2.5, used by generation in M5)."""
from __future__ import annotations


def rollup(links: dict[int, list[int]], leaf_values: dict[int, float]) -> dict[int, float]:
    out = dict(leaf_values)
    for t in sorted(links):                    # children always come before their total
        out[t] = sum(out[c] for c in links[t])
    return out


def pct_of_total(values: list[float], total: float) -> list[float]:
    return [100.0 * v / total if total else 0.0 for v in values]
