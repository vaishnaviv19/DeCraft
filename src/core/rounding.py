# src/core/rounding.py
"""Display Rounding and Total Mode (M2.5, M8.1). Round once, half up, at the column's Decimals."""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd


def display_round(x: float, d: int) -> float:
    """Half up, never Python's built-in round (banker's rounding)."""
    return float(Decimal(str(x)).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP))


def largest_remainder(values: list[float], total: float = 100.0, decimals: int = 1) -> list[float]:
    """Round percentages so that they still sum to `total`."""
    scale = 10 ** decimals
    raw = [v * scale for v in values]
    floors = [math.floor(r) for r in raw]
    deficit = int(round(total * scale)) - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in order[:max(deficit, 0)]:
        floors[i] += 1
    return [f / scale for f in floors]


def detect_total_mode(groups: list[tuple[list[float], float]], d: int) -> str:
    """groups: (displayed components, displayed total) pairs read from the source table."""
    if not groups:
        return "true_total"
    ok = sum(abs(sum(c) - t) < 0.5 * 10 ** -d for c, t in groups)
    return "sum_of_displayed" if ok / len(groups) >= 0.8 else "true_total"


def build_display_df(full: pd.DataFrame, decimals: dict[str, int],
                     links: dict[int, list[int]], mode: str) -> pd.DataFrame:
    """The rounded, reader-facing copy. In `sum_of_displayed` mode totals are re-added after rounding."""
    disp = full.copy()
    for col, d in decimals.items():
        disp[col] = disp[col].map(lambda v: display_round(v, d))
    if mode == "sum_of_displayed":
        for t in sorted(links):
            for col, d in decimals.items():
                disp.loc[t, col] = display_round(sum(disp.loc[c, col] for c in links[t]), d)
    return disp
