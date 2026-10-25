# src/validation/_utility.py
"""Utility gate (decision D-18). FAIL: a trend direction flips or a strong correlation changes sign.
WARN: ranges, category shares or correlation strength drift beyond tolerance."""
from __future__ import annotations

import pandas as pd

from src.contracts import Severity, Violation

OPPOSITE = {("up", "down"), ("down", "up"), ("positive", "negative"), ("negative", "positive")}


def pattern_gate(pattern_id: str, source_direction: str, synthetic_direction: str,
                 source_is_confirmed: bool) -> list[Violation]:
    if source_is_confirmed and (source_direction, synthetic_direction) in OPPOSITE:
        return [Violation(check="utility_direction", severity=Severity.ERROR, rule_id=pattern_id,
                          message=f"direction flipped: source {source_direction}, "
                                  f"synthetic {synthetic_direction}", repairable=True)]
    return []


def correlation_strength_warning(pattern_id: str, source_rho: float, synthetic_rho: float,
                                 th) -> list[Violation]:
    if abs(source_rho - synthetic_rho) > th.corr_tolerance_small_n:
        return [Violation(check="utility_corr_strength", severity=Severity.WARNING, rule_id=pattern_id,
                          message=f"correlation moved by {abs(source_rho - synthetic_rho):.2f}",
                          repairable=False)]
    return []


def range_warnings(table_id: str, column: str, source: pd.Series, synthetic: pd.Series,
                   th) -> list[Violation]:
    lo, hi = source.min(), source.max()
    span = hi - lo if hi != lo else abs(hi) or 1.0
    if synthetic.min() < lo - th.utility_range_widen * span \
            or synthetic.max() > hi + th.utility_range_widen * span:
        return [Violation(check="utility_range", severity=Severity.WARNING, table_id=table_id,
                          column=column,
                          message="generated values outside the widened source range",
                          repairable=False)]
    return []


def category_share_warnings(table_id: str, column: str, source: pd.Series,
                            synthetic: pd.Series, th) -> list[Violation]:
    a, b = source.value_counts(normalize=True), synthetic.value_counts(normalize=True)
    shared = len(set(a.index) & set(b.index)) / max(min(len(a), len(b)), 1)
    if shared < 0.5:
        # the labels were replaced with fakes: compare the SHAPE (largest share vs largest share ...)
        n = max(len(a), len(b))
        a = pd.Series(sorted(a, reverse=True) + [0.0] * (n - len(a)))
        b = pd.Series(sorted(b, reverse=True) + [0.0] * (n - len(b)))
        diff = (a - b).abs()
    else:
        index = a.index.union(b.index)
        diff = (a.reindex(index, fill_value=0) - b.reindex(index, fill_value=0)).abs()
    if (diff > th.utility_category_share_tol).any():
        return [Violation(check="utility_category_share", severity=Severity.WARNING,
                          table_id=table_id, column=column,
                          message=f"category share drift up to {diff.max():.2f}", repairable=False)]
    return []
