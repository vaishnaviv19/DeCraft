# src/generation/_copula.py
"""Correlated residual generation (M6.4, B-05).

Two series that really move together must still move together after generation. Residuals are sampled
jointly with a Gaussian copula fitted on the source residuals, added back to fresh trend lines, and the
result is re-measured with retries. Weak relationships are never strengthened (D-06: unmet is recorded).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm, spearmanr

from src.core.stats import residual_correlation, theil_sen
from src.generation._tiny import safe_level_factor


@dataclass
class CorrelatedSeries:
    x: list[float]
    y: list[float]
    met: bool
    attempts: int


def _residuals(values: np.ndarray) -> tuple[np.ndarray, float, float]:
    slope, intercept = theil_sen(values)
    steps = np.arange(len(values), dtype=float)
    return values - (intercept + slope * steps), slope, intercept


def generate_correlated(x_source, y_source, th, rng: np.random.Generator,
                        min_distance: float = 0.05) -> CorrelatedSeries:
    pairs = [(a, b) for a, b in zip(x_source, y_source)
             if a is not None and b is not None and np.isfinite(a) and np.isfinite(b)]
    if len(pairs) < th.min_rows_corr:
        return CorrelatedSeries([float(a) if a is not None else 0.0 for a in x_source],
                                [float(b) if b is not None else 0.0 for b in y_source], False, 0)
    xs = np.asarray([a for a, _ in pairs], dtype=float)
    ys = np.asarray([b for _, b in pairs], dtype=float)
    residual_x, slope_x, intercept_x = _residuals(xs)
    residual_y, slope_y, intercept_y = _residuals(ys)
    if np.std(residual_x) == 0 or np.std(residual_y) == 0:
        return CorrelatedSeries(list(xs), list(ys), False, 0)
    rho, _ = spearmanr(residual_x, residual_y)
    if not np.isfinite(rho):
        return CorrelatedSeries(list(xs), list(ys), False, 0)
    if abs(rho) < 0.5:
        # A weak relationship stays weak: do not manufacture co-movement (B-05).
        return CorrelatedSeries(list(xs), list(ys), False, 0)
    n = len(xs)
    best: CorrelatedSeries | None = None
    for attempt in range(1, th.max_generation_tries + 1):
        z = rng.multivariate_normal([0.0, 0.0], [[1.0, rho], [rho, 1.0]], size=n)
        u = norm.cdf(z)
        ex = np.quantile(residual_x, u[:, 0])
        ey = np.quantile(residual_y, u[:, 1])
        factor_x = safe_level_factor(rng, th.jitter_low, th.jitter_high, min_distance)
        factor_y = safe_level_factor(rng, th.jitter_low, th.jitter_high, min_distance)
        steps = np.arange(n, dtype=float)
        new_x = xs.mean() * factor_x + slope_x * steps + ex * 0.5
        new_y = ys.mean() * factor_y + slope_y * steps + ey * 0.5
        for series, original in ((new_x, xs), (new_y, ys)):
            for i in range(n):
                if original[i] and abs(series[i] - original[i]) < min_distance * abs(original[i]):
                    sign = 1.0 if series[i] >= original[i] else -1.0
                    series[i] = original[i] + sign * min_distance * abs(original[i]) * 1.2
        direction, measured, _, uncertain = residual_correlation(new_x, new_y, th)
        expected = "positive" if rho > 0 else "negative"
        met = direction == expected and not uncertain and abs(measured - rho) <= 0.3
        candidate = CorrelatedSeries(list(new_x), list(new_y), met, attempt)
        if best is None or (met and not best.met) or (met == best.met and attempt == 1):
            best = candidate
        if met:
            return candidate
    return best or CorrelatedSeries(list(xs), list(ys), False, th.max_generation_tries)
