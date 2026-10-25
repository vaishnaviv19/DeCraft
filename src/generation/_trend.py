# src/generation/_trend.py
"""Trend-preserving generation (M6.4, D-06).

Keep the direction, give the series a new start level and a slope within +/-40% of the source slope,
then re-classify. The best attempt is always returned; an unmet target is recorded, never forced.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.core.segments import SegmentShape, detect_segments
from src.core.stats import classify_trend
from src.generation._tiny import safe_level_factor

SLOPE_FACTOR = 0.40


@dataclass
class GeneratedSeries:
    values: list[float | None]
    met: bool
    attempts: int


def generate_with_shape(source: list[float | None], direction: str, th,
                        rng: np.random.Generator, min_distance: float = 0.05) -> GeneratedSeries:
    clean = [float(v) for v in source if v is not None and np.isfinite(v)]
    n = len(source)
    if len(clean) < 2:
        return GeneratedSeries([None if v is None else float(v) for v in source], False, 0)
    y = np.asarray(clean)
    level = float(np.mean(y[: max(2, n // 3)]))
    step = float(np.mean(np.diff(y))) if len(y) > 1 else 0.0
    minimal = abs(float(np.mean(y))) * 0.03 + 1e-9
    if direction == "up":
        step = max(abs(step), minimal)
    elif direction == "down":
        step = -max(abs(step), minimal)
    noise = float(np.std(np.diff(y))) if len(y) > 1 else minimal

    best_values: list[float | None] | None = None
    for attempt in range(1, th.max_generation_tries + 1):
        start = level * safe_level_factor(rng, th.jitter_low, th.jitter_high, min_distance)
        slope = step * (1 + rng.uniform(-SLOPE_FACTOR, SLOPE_FACTOR))
        values = [start + slope * i + rng.normal(0.0, noise * 0.25) for i in range(n)]
        for i, old in enumerate(source):
            if old is None or not np.isfinite(old):
                values[i] = None if old is None else float(old)
                continue
            if abs(values[i] - old) < min_distance * abs(old):
                direction_sign = 1.0 if values[i] >= old else -1.0
                values[i] = old + direction_sign * min_distance * abs(old) * 1.2
        if best_values is None:
            best_values = values
        classified = classify_trend(values, th)[0]
        if classified == direction:
            return GeneratedSeries(values, True, attempt)
    return GeneratedSeries(best_values or list(source), False, th.max_generation_tries)


def generate_with_segments(source: list[float | None], shape: SegmentShape, th,
                           rng: np.random.Generator, min_distance: float = 0.05) -> GeneratedSeries:
    """Keep the segment directions. The shape is re-detected after each attempt; unmet is recorded."""
    clean = [float(v) for v in source if v is not None and np.isfinite(v)]
    if len(clean) < 2 or not shape.segments:
        return GeneratedSeries([None if v is None else float(v) for v in source], False, 0)
    base = np.asarray(clean)
    noise = float(np.std(np.diff(base))) * 0.25 if len(base) > 1 else 0.0
    best_values = None
    for attempt in range(1, th.max_generation_tries + 1):
        factor = safe_level_factor(rng, th.jitter_low, th.jitter_high, min_distance)
        values = base * factor + rng.normal(0.0, noise, len(base))
        values = np.asarray(values, dtype=float)
        for i, old in enumerate(clean):
            if abs(values[i] - old) < min_distance * abs(old):
                sign = 1.0 if values[i] >= old else -1.0
                values[i] = old + sign * min_distance * abs(old) * 1.2
        if best_values is None:
            best_values = values
        detected = detect_segments(values, th, rng=None, permutations=0)
        if detected.directions == shape.directions and detected.tier != "uncertain":
            return GeneratedSeries(list(values), True, attempt)
    return GeneratedSeries(list(best_values) if best_values is not None else list(base), False,
                           th.max_generation_tries)
