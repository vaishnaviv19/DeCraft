# src/core/aggregation.py
"""Cross-table Aggregate Rules (M3.3): a summary column equal to an aggregate of a detail column."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

AGGS = ("sum", "mean", "count", "max", "min")      # order = preference on ties


@dataclass
class AggregateRule:
    detail: str
    summary: str
    group_col: str
    label_col: str
    detail_col: str
    summary_col: str
    agg: str
    match_rate: float
    n_rows: int


def _text_cols(df: pd.DataFrame):
    return [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]


def detect_aggregate_rules(tables: dict[str, pd.DataFrame], rel_tol: float = 0.005, min_rows: int = 3,
                           min_match: float = 0.9, min_overlap: float = 0.6) -> list[AggregateRule]:
    best: dict[tuple, AggregateRule] = {}
    for dn, d in tables.items():
        for sn, s in tables.items():
            if dn == sn:
                continue
            for lc in _text_cols(s):
                labels = s[lc].dropna().astype(str)
                for gc in _text_cols(d):
                    if len(labels) < min_rows or labels.isin(set(d[gc].astype(str))).mean() < min_overlap:
                        continue
                    for dc in d.select_dtypes("number").columns:
                        for agg in AGGS:
                            grouped = d.groupby(d[gc].astype(str))[dc].agg(agg)
                            for sc in s.select_dtypes("number").columns:
                                expected = s[lc].astype(str).map(grouped)
                                valid = expected.notna() & s[sc].notna()
                                if valid.sum() < min_rows:
                                    continue
                                ok = (expected - s[sc]).abs() <= rel_tol * s[sc].abs().clip(lower=1e-9)
                                rate = float(ok[valid].mean())
                                key = (sn, sc)
                                if rate >= min_match and (key not in best or rate > best[key].match_rate):
                                    best[key] = AggregateRule(dn, sn, gc, lc, dc, sc, agg, rate,
                                                              int(valid.sum()))
    return list(best.values())


def apply_aggregate_rule(rule: AggregateRule, detail: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    grouped = detail.groupby(detail[rule.group_col].astype(str))[rule.detail_col].agg(rule.agg)
    out = summary.copy()
    out[rule.summary_col] = out[rule.label_col].astype(str).map(grouped).fillna(0.0)
    return out


def detect_scalar_aggregate(detail: pd.DataFrame, col: str, value: float, rel_tol: float = 0.005):
    """A single headline number (for example group revenue) that equals the sum or mean of a detail column."""
    for agg in ("sum", "mean"):
        if abs(getattr(detail[col], agg)() - value) <= rel_tol * max(abs(value), 1e-9):
            return agg
    return None
