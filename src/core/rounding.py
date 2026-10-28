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


def build_display_df(full: pd.DataFrame, decimals: dict[str, int],
                     links: dict[int, list[int]], mode: str,
                     scales: dict[str, float] | None = None) -> pd.DataFrame:
    """The rounded, reader-facing copy, in the SOURCE's units: a column shown as 'Rs cr' is divided back
    by its Scale, then rounded once. In `sum_of_displayed` mode totals are re-added after rounding."""
    disp = full.copy()
    for col, scale in (scales or {}).items():
        if col in disp.columns and scale not in (None, 0, 1) and pd.api.types.is_numeric_dtype(disp[col]):
            disp[col] = disp[col] / scale
    for col, d in decimals.items():
        disp[col] = disp[col].map(lambda v: display_round(v, d))
    if mode == "sum_of_displayed":
        for t in sorted(links):
            for col, d in decimals.items():
                disp.loc[t, col] = display_round(sum(disp.loc[c, col] for c in links[t]), d)
    return disp
