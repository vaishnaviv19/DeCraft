# src/generation/_tiny.py
"""Jitter Policy for Tiny Tables (decision D-11).

A table of at most `tiny_table_max_rows` rows cannot support a fitted distribution, so one Safe Factor
per root numeric column shifts the whole column away from the source, and a small wobble varies the
cells. Every cell must end at least `min_distance` from its source cell.
"""
from __future__ import annotations

import numpy as np

from src.core.distance import ensure_distance
from src.core.periods import parse_period
from src.core.values import displayed_decimals, format_number_like, parse_number


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


def perturb_text_numbers(values: list, rng: np.random.Generator, low: float = 0.60, high: float = 1.60,
                         min_distance: float = 0.05, wobble: float = 0.03) -> list:
    """Numbers kept as text in a label column (stacked tables: 'Rs. 86,856', '53.0%') are data, so they are
    fabricated like a tiny table: one Safe Factor for the column plus a wobble, the distance kept after
    rounding to the cell's own decimals, and the cell's own format kept. Word labels pass through."""
    factor = safe_level_factor(rng, low, high, min_distance)
    out = []
    for value in values:
        number = None if value is None else parse_number(value)
        if number is None or parse_period(str(value)) is not None:
            out.append(value)
            continue
        decimals = displayed_decimals(value) or 0
        candidate = (number * factor * (1 + rng.uniform(-wobble, wobble)) if number
                     else float(rng.uniform(min_distance, 2 * min_distance)))
        out.append(format_number_like(str(value), ensure_distance(float(candidate), number, min_distance, decimals)))
    return out



# ---------------------------------------------------------------- v3.3 F-01: tiny tables by cell
def _shares(values: np.ndarray) -> np.ndarray | None:
    total = np.nansum(np.abs(values))
    return None if total == 0 else np.abs(values) / total


def shares_far_enough(src: np.ndarray, new: np.ndarray, min_rel: float) -> bool:
    """Every row's share of the column total differs >= `min_rel` (relative) from its source share.
    A column with fewer than 2 non-zero rows is exempt (a single row is always 100%)."""
    src, new = np.asarray(src, dtype=float), np.asarray(new, dtype=float)
    ok = ~np.isnan(src) & (src != 0)
    if ok.sum() < 2:
        return True
    s, n = _shares(src[ok]), _shares(new[ok])
    return bool(np.all(np.abs(n / s - 1) >= min_rel))


def jitter_cells_by_cell(values: list[float | None], rng: np.random.Generator, low: float = 0.60,
                         high: float = 1.60, min_distance: float = 0.05, min_share: float = 0.10,
                         max_tries: int = 200) -> list[float | None]:
    """F-01 (replaces D-11 for tiny tables): an independent Safe Factor PER CELL, re-drawn until every
    cell is >= min_distance from its source cell AND every row's share of the column total moves
    >= min_share. One factor per column kept every share within ~3% of the source, so the share facts
    failed the 10% Headline Distance and repair looped. Best effort after `max_tries` (the audit
    reports what remains)."""
    src = np.array([np.nan if v is None else float(v) for v in values])
    best, best_score = None, -1.0
    for _ in range(max_tries):
        factors = np.array([safe_level_factor(rng, low, high, min_distance) for _ in src])
        new = np.where(np.isnan(src), np.nan, np.where(src == 0, 0.0, src * factors))
        if shares_far_enough(src, new, min_share):
            best = new
            break
        ok = ~np.isnan(src) & (src != 0)
        score = float(np.min(np.abs(_shares(new[ok]) / _shares(src[ok]) - 1))) if ok.sum() >= 2 else 1.0
        if score > best_score:
            best, best_score = new, score
    out: list[float | None] = []
    for value, candidate in zip(values, best):
        if value is None:
            out.append(None)
        elif value == 0:
            out.append(float(rng.uniform(min_distance, 2 * min_distance)))
        else:
            out.append(float(candidate))
    return out


def jitter_table(df, root_numeric: list[str], th, rng: np.random.Generator):
    """A tiny table with every root numeric column jittered by cell (F-01); derived columns are left
    for the generation plan to recompute."""
    out = df.copy()
    for column in root_numeric:
        values = [None if v != v else float(v) for v in df[column].astype(float)]
        out[column] = jitter_cells_by_cell(values, rng, th.jitter_low, th.jitter_high,
                                           th.min_value_distance, th.min_headline_distance)
    return out
