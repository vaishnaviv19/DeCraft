# src/generation/_distributions.py
"""Fitted numeric distributions for tables larger than the tiny-table limit (M5.3).

The family is chosen from the data (skewed positive data -> lognormal), fitted to the source, and
sampled with the Noise Budget. Every root cell is then pushed at least `min_distance` away from its
source counterpart (D-12).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def fit_family(values: np.ndarray) -> str:
    if len(values) < 3:
        return "normal"
    skew = float(pd.Series(values).skew())
    if skew > 1.0 and (values > 0).all():
        return "lognormal"
    return "normal"


def _widen(values: np.ndarray, widen: float) -> tuple[float, float]:
    lo, hi = float(values.min()), float(values.max())
    span = hi - lo
    return lo - widen * span, hi + widen * span


def sample_numeric(values: list[float | None], n: int, rng: np.random.Generator,
                   min_distance: float = 0.05, widen: float = 0.6,
                   noise: float | None = None) -> list[float | None]:
    """Draw `n` values from the fitted family; `values` may be shorter than `n` (aligned by position)."""
    present = np.asarray([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    if len(present) == 0:
        return [None] * n
    family = fit_family(present)
    if family == "lognormal":
        logs = np.log(present)
        draws = np.exp(rng.normal(logs.mean(), logs.std(ddof=0) or 0.1, n))
    else:
        std = present.std(ddof=0) or (abs(present.mean()) * 0.1 + 1.0)
        draws = rng.normal(present.mean(), std, n)
    if noise:
        draws = draws * (1 + rng.uniform(-noise, noise, n))
    lo, hi = _widen(present, widen)
    if present.min() >= 0:
        lo = max(lo, 0.0)
    draws = np.clip(draws, lo, hi)

    out: list[float | None] = []
    for i in range(n):
        new = float(draws[i])
        old = values[i] if i < len(values) else None
        if old is not None and old != 0 and abs(new - old) / abs(old) < min_distance:
            direction = 1.0 if new >= old else -1.0
            new = old + direction * min_distance * abs(old) * 1.2
        out.append(new)
    return out
