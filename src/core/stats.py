# src/core/stats.py
"""Trends and Residual Correlation (M6.1, M6.2).

A Trend is the overall direction of a series from Kendall tau and a Theil-Sen slope. Correlation
between two time-indexed series is measured on Theil-Sen residuals, so two unrelated rising series are
not called correlated (decision D-15).
"""
from __future__ import annotations

import numpy as np
from scipy.stats import kendalltau, spearmanr, theilslopes

from src.core.periods import Period


def theil_sen(values) -> tuple[float, float]:
    """Robust slope and intercept per step."""
    y = np.asarray(values, dtype=float)
    x = np.arange(len(y), dtype=float)
    slope, intercept, _, _ = theilslopes(y, x)
    return float(slope), float(intercept)


def _clean(values) -> list[float]:
    return [float(v) for v in values if v is not None and np.isfinite(v)]


def classify_trend(values, th) -> tuple[str, float | None, float | None, float | None]:
    """-> (direction, kendall_tau, p_value, slope). Fewer than trend_min_points -> 'uncertain'.

    A rising series may contain dips: the direction comes from tau and the slope, not from every step.
    """
    clean = _clean(values)
    if len(clean) < th.trend_min_points:
        return "uncertain", None, None, None
    y = np.asarray(clean)
    tau, p = kendalltau(np.arange(len(y)), y)
    slope, _ = theil_sen(y)
    if tau is not None and np.isfinite(tau) and abs(tau) >= th.trend_tau_min and p <= th.trend_p_max:
        return ("up" if slope >= 0 else "down"), float(tau), float(p), slope
    span = float(np.ptp(y)) or abs(float(np.mean(y))) or 1.0
    if abs(slope * (len(y) - 1)) <= th.flat_pct * span:
        return "flat", float(tau) if tau is not None else None, float(p) if p is not None else None, slope
    return "mixed", float(tau) if tau is not None else None, float(p) if p is not None else None, slope


def residual_correlation(x, y, th) -> tuple[str, float | None, float | None, bool]:
    """-> (direction, spearman_rho on residuals, p_value, uncertain)."""
    pairs = [(a, b) for a, b in zip(x, y)
             if a is not None and b is not None and np.isfinite(a) and np.isfinite(b)]
    if len(pairs) < th.min_rows_corr:
        return "uncertain", None, None, True
    xs = np.asarray([a for a, _ in pairs], dtype=float)
    ys = np.asarray([b for _, b in pairs], dtype=float)
    slope_x, intercept_x = theil_sen(xs)
    slope_y, intercept_y = theil_sen(ys)
    steps = np.arange(len(xs))
    residual_x = xs - (intercept_x + slope_x * steps)
    residual_y = ys - (intercept_y + slope_y * steps)
    rho, p = spearmanr(residual_x, residual_y)
    if rho is None or not np.isfinite(rho):
        return "uncertain", None, None, True
    if p <= 0.05:
        return ("positive" if rho > 0 else "negative"), float(rho), float(p), False
    if abs(rho) < 0.3:
        return "none", float(rho), float(p), False
    return "uncertain", float(rho), float(p), True


def period_ordinal(period: Period) -> int:
    return period.ordinal
