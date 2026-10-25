# src/core/segments.py
"""Structural Breaks (M6b, fixes B-04).

A Segment Shape is 1-3 straight segments separated by confirmed breaks. A break must be big relative to
the noise, persist, improve BIC by a margin and beat a permutation test. Minimum segment length 3; a
single spike and a pure trend are not breaks. Hard gate: at most 5% false positives on noise.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel

MIN_SEGMENT = 3


class Segment(BaseModel):
    start: int
    end: int                       # inclusive
    direction: Literal["up", "down", "flat"]
    start_level: float
    end_level: float
    pct_change: float


class SegmentShape(BaseModel):
    n: int
    segments: list[Segment]
    tier: Literal["confirmed", "probable", "uncertain"]
    p_value: float | None = None
    loo_fraction: float | None = None
    reasons: list[str] = []

    @property
    def directions(self) -> list[str]:
        return [s.direction for s in self.segments]

    @property
    def break_indices(self) -> list[int]:
        return [s.start for s in self.segments[1:]]


def _line(y: np.ndarray) -> tuple[float, float, float]:
    x = np.arange(len(y), dtype=float)
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    slope = float(((x - xm) * (y - ym)).sum() / sxx) if sxx else 0.0
    intercept = float(ym - slope * xm)
    residual = y - (slope * x + intercept)
    return slope, intercept, float(residual @ residual)


def _bic(n: int, sse: float, params: int) -> float:
    return n * np.log(max(sse, 1e-12) / n) + params * np.log(n)


def _search(y: np.ndarray, breaks: int):
    """(BIC of one segment, (BIC, break indices) of the best model with `breaks` breaks)."""
    n = len(y)
    base = _bic(n, _line(y)[2], 2)
    best = None
    if breaks == 1:
        for i in range(MIN_SEGMENT, n - MIN_SEGMENT + 1):
            sse = _line(y[:i])[2] + _line(y[i:])[2]
            bic = _bic(n, sse, 5)
            if best is None or bic < best[0]:
                best = (bic, [i])
        return base, best
    for i in range(MIN_SEGMENT, n - 2 * MIN_SEGMENT + 1):
        for j in range(i + MIN_SEGMENT, n - MIN_SEGMENT + 1):
            sse = _line(y[:i])[2] + _line(y[i:j])[2] + _line(y[j:])[2]
            bic = _bic(n, sse, 8)
            if best is None or bic < best[0]:
                best = (bic, [i, j])
    return base, best


def _direction(y: np.ndarray, flat_pct: float) -> str:
    slope, _, _ = _line(y)
    span = float(np.ptp(y)) or abs(float(np.mean(y))) or 1.0
    if abs(slope * max(len(y) - 1, 1)) <= flat_pct * span:
        return "flat"
    return "up" if slope > 0 else "down"


def _make_segments(y: np.ndarray, breaks: list[int], flat_pct: float) -> list[Segment]:
    bounds = [0, *breaks, len(y)]
    out = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a < 1:
            continue
        slope, intercept, _ = _line(y[a:b])
        start_level = float(intercept)
        end_level = float(intercept + slope * (b - a - 1))
        change = (end_level - start_level) / abs(start_level) if start_level else 0.0
        out.append(Segment(start=a, end=b - 1, direction=_direction(y[a:b], flat_pct),
                           start_level=start_level, end_level=end_level, pct_change=100.0 * change))
    return out


def _accept(y: np.ndarray, index: int, th) -> bool:
    """A break must be a sizeable level jump or a sizeable slope change at that point."""
    n = len(y)
    persist = max(th.break_min_persist, 2)
    left, right = y[max(0, index - persist):index], y[index:index + persist]
    if not len(left) or not len(right):
        return False
    noise = float(np.std(np.diff(y))) or 1e-9
    jump = abs(float(right.mean() - left.mean()))
    span = max(MIN_SEGMENT, persist + 1)
    slope_left = _line(y[max(0, index - span):index])[0] if index >= 2 else 0.0
    slope_right = _line(y[index:index + span])[0] if n - index >= 2 else 0.0
    return jump >= th.break_size_mult * noise \
        or abs(slope_right - slope_left) >= th.break_size_mult * noise / span


def detect_segments(values, th, rng: np.random.Generator | None = None,
                    permutations: int = 19) -> SegmentShape:
    y = np.asarray([float(v) for v in values if v is not None and np.isfinite(v)])
    n = len(y)
    if n < 2:
        return SegmentShape(n=n, segments=[], tier="uncertain", reasons=["not enough points"])
    single = _make_segments(y, [], th.flat_pct)
    if n < th.break_min_points:
        return SegmentShape(n=n, segments=single, tier="uncertain",
                            reasons=["fewer than break_min_points"])

    base, two = _search(y, 1)
    _, three = _search(y, 2)
    candidates = [(two, 1), (three, 2)]
    chosen = min((c for c in candidates if c[0] is not None), key=lambda c: c[0][0], default=None)
    if chosen is None:
        return SegmentShape(n=n, segments=single, tier="uncertain", reasons=["no split possible"])
    (best_bic, breaks), k = chosen
    gain = base - best_bic
    if gain < th.break_bic_margin:
        return SegmentShape(n=n, segments=single, tier="uncertain", reasons=["no BIC improvement"])
    if not all(_accept(y, b, th) for b in breaks):
        return SegmentShape(n=n, segments=single, tier="uncertain",
                            reasons=["break too small relative to noise"])

    p_value = None
    if rng is not None and permutations:
        hits = 0
        for _ in range(permutations):
            shuffled = rng.permutation(y)
            shuffled_base, shuffled_best = _search(shuffled, k)
            shuffled_gain = shuffled_base - (shuffled_best[0] if shuffled_best else shuffled_base)
            hits += shuffled_gain >= gain
        p_value = (1 + hits) / (1 + permutations)
        if p_value > th.break_p_max:
            return SegmentShape(n=n, segments=single, tier="uncertain", p_value=p_value,
                                reasons=["permutation test failed"])

    segments = _make_segments(y, breaks, th.flat_pct)
    tier = "confirmed" if p_value is not None and p_value <= th.break_p_max else "probable"
    return SegmentShape(n=n, segments=segments, tier=tier, p_value=p_value,
                        reasons=[f"break at {b}" for b in breaks])
