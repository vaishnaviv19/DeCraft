# src/core/distance.py
"""Distance and overlap checks (Shared Kernel): used by the privacy audit and by validation (D-12, D-13)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.contracts import Severity, Violation


def rel_diff(a: float, b: float) -> float:
    if b == 0:
        return abs(a)
    return abs(a - b) / abs(b)


def ensure_distance(new: float | None, old: float | None, min_rel: float,
                    decimals: int | None = None, push: float = 1.05) -> float | None:
    """`new` moved (only if needed) so that it stays >= min_rel away from `old` even AFTER rounding to
    `decimals` places for display. 3 -> 3.16 would print as 3 (the source value), so it becomes 4."""
    if new is None or old is None or not np.isfinite(new) or not np.isfinite(old):
        return new

    def far(x: float) -> bool:
        shown = x if decimals is None else round(x, decimals)
        return rel_diff(shown, old) >= min_rel

    if far(new):
        return new
    direction = 1.0 if new >= old else -1.0
    candidate = old + direction * max(min_rel * abs(old), min_rel if old == 0 else 0.0) * push
    if decimals is not None:
        step = 10.0 ** -decimals
        candidate = round(candidate, decimals)
        for _ in range(10_000):                           # each step moves one display unit
            if far(candidate):
                break
            candidate = round(candidate + direction * step, decimals)
    return float(candidate)


def aligned(source: pd.DataFrame, synthetic: pd.DataFrame, column: str,
            key: str | None) -> tuple[pd.Series, pd.Series]:
    """Generated vs source values for one column, aligned on the row key (or by row order).
    Generation enforces distance per row POSITION, so when the keys were regenerated (no generated
    key equals a source key) the rows are compared by position instead of comparing nothing."""
    if key and key in source.columns and key in synthetic.columns:
        merged = source[[key, column]].merge(synthetic[[key, column]], on=key, suffixes=("_s", "_g"))
        if not merged.empty:
            return merged[f"{column}_g"], merged[f"{column}_s"]
    n = min(len(source), len(synthetic))
    return synthetic[column].iloc[:n], source[column].iloc[:n]


def value_distance_violations(table_id: str, source: pd.DataFrame, synthetic: pd.DataFrame,
                              key: str | None, value_cols: list[str], min_rel: float
                              ) -> list[Violation]:
    out = []
    for col in value_cols:
        if col not in source.columns or col not in synthetic.columns:
            continue
        generated, original = aligned(source, synthetic, col, key)
        a, b = pd.to_numeric(generated, errors="coerce"), pd.to_numeric(original, errors="coerce")
        both = a.notna() & b.notna() & (b != 0)
        close = both & ((a - b).abs() / b.abs() < min_rel)
        if close.any():
            out.append(Violation(
                check="value_distance", severity=Severity.ERROR, table_id=table_id, column=col,
                rows=[int(i) for i in np.flatnonzero(close.to_numpy())],
                message=f"{int(close.sum())} cells within {min_rel:.0%} of the source value",
                repairable=True))
    return out


def quasi_row_matches(table_id: str, source: pd.DataFrame, synthetic: pd.DataFrame,
                      quasi: list[str]) -> list[Violation]:
    if not quasi or any(c not in source.columns or c not in synthetic.columns for c in quasi):
        return []
    merged = (synthetic[quasi].astype(str).reset_index()
              .merge(source[quasi].astype(str).drop_duplicates(), on=quasi))
    if merged.empty:
        return []
    return [Violation(
        check="quasi_identifier_match", severity=Severity.ERROR, table_id=table_id,
        rows=merged["index"].astype(int).tolist(),
        message=f"{len(merged)} generated rows match a source row on all quasi-identifiers",
        repairable=True)]


def headline_violations(source_facts: dict[str, float], synthetic_facts: dict[str, float],
                        min_rel: float) -> list[Violation]:
    out = []
    for key, value in synthetic_facts.items():
        if key in source_facts and isinstance(value, (int, float)) \
                and rel_diff(value, source_facts[key]) < min_rel:
            out.append(Violation(
                check="headline_distance", severity=Severity.ERROR, column=key,
                message=f"fact {key} is within {min_rel:.0%} of the source figure", repairable=True))
    return out


def rare_combination_violations(table_id: str, synthetic: pd.DataFrame, quasi: list[str],
                                th) -> list[Violation]:
    if len(synthetic) < th.k_anonymity_min_rows or not quasi:
        return []                                   # decision D-12: no k-anonymity under 20 rows
    sizes = synthetic.groupby(quasi).size()
    rare = sizes[sizes < th.k_anonymity_k]
    if rare.empty:
        return []
    return [Violation(
        check="rare_combination", severity=Severity.WARNING, table_id=table_id,
        message=f"{len(rare)} quasi-identifier combinations are seen fewer than "
                f"{th.k_anonymity_k} times",
        repairable=True)]
