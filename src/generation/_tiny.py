# src/generation/_tiny.py
"""Jitter Policy for Tiny Tables (decision D-11).

A table of at most `tiny_table_max_rows` rows cannot support a fitted distribution, so one Safe Factor
per root numeric column shifts the whole column away from the source, and a small wobble varies the
cells. Every cell must end at least `min_distance` from its source cell.
"""
from __future__ import annotations

import numpy as np

from src.core.distance import ensure_distance


def safe_level_factor(rng: np.random.Generator, low: float = 0.60, high: float = 1.60,
                      min_distance: float = 0.05, max_tries: int = 10_000) -> float:
    """A multiplier from the jitter band that is never within +/-min_distance of 1.0."""
    for _ in range(max_tries):
        factor = float(rng.uniform(low, high))
        if abs(factor - 1.0) >= min_distance:
            return factor
    raise RuntimeError("jitter band has no room outside the exclusion zone")


def jitter_cells(values: list[float | None], rng: np.random.Generator, low: float = 0.60,
                 high: float = 1.60, min_distance: float = 0.05, wobble: float = 0.03,
                 decimals: int | None = None) -> list[float | None]:
    """Apply one Safe Factor plus a +/-wobble to every cell; distance is enforced per cell, and with
    `decimals` it still holds after rounding for display (3 -> 3.4 would print as the source 3)."""
    factor = safe_level_factor(rng, low, high, min_distance)
    out: list[float | None] = []
    for value in values:
        if value is None:
            out.append(None)
            continue
        if value == 0:
            zero = float(rng.uniform(min_distance, 2 * min_distance))
            out.append(zero if decimals is None else ensure_distance(zero, 0.0, min_distance, decimals))
            continue
        candidate = value * factor * (1 + rng.uniform(-wobble, wobble))
        if abs(candidate - value) < min_distance * abs(value):
            direction = 1.0 if factor >= 1.0 else -1.0
            candidate = value + direction * min_distance * abs(value) * 1.05
        out.append(float(candidate) if decimals is None
                   else ensure_distance(float(candidate), value, min_distance, decimals))
    return out


def scale_cells(values: list[float | None], factor: float, rng: np.random.Generator, wobble: float = 0.03,
                min_distance: float = 0.05, decimals: int | None = None) -> list[float | None]:
    """Every cell x factor x (1 +/- wobble), kept >= min_distance from its source cell. With ONE factor
    for a whole statement, row arithmetic and year-over-year movement both survive."""
    out: list[float | None] = []
    for value in values:
        if value is None:
            out.append(None)
            continue
        candidate = value * factor * (1 + rng.uniform(-wobble, wobble)) if value else \
            float(rng.uniform(min_distance, 2 * min_distance))
        out.append(ensure_distance(float(candidate), value, min_distance, decimals))
    return out
