# src/core/aggregation.py
"""Cross-table Aggregate Rules (M3.3): a summary column equal to an aggregate of a detail column."""
from __future__ import annotations

import re
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


def aggregate_problem(d: pd.DataFrame, s: pd.DataFrame, group_col: str, label_col: str, detail_col: str,
                      summary_col: str, agg: str, min_rows: int = 3, min_overlap: float = 0.6) -> str | None:
    """None when the combination can be tested, else why not (shape only; no values are compared)."""
    if agg not in AGGS:
        return f"unsupported aggregate {agg!r}"
    if not pd.api.types.is_numeric_dtype(d[detail_col]) or not pd.api.types.is_numeric_dtype(s[summary_col]):
        return "detail and summary columns must hold numbers"
    if pd.api.types.is_numeric_dtype(d[group_col]) or pd.api.types.is_numeric_dtype(s[label_col]):
        return "group and label columns must hold labels, not numbers"
    return _labels_problem(d, s, group_col, label_col, min_rows, min_overlap)


def _labels_problem(d, s, group_col, label_col, min_rows, min_overlap) -> str | None:
    labels = s[label_col].dropna().astype(str)
    if len(labels) < min_rows:
        return f"fewer than {min_rows} summary labels"
    if labels.isin(set(d[group_col].astype(str))).mean() < min_overlap:
        return "summary labels do not match the detail table's groups"
    return None


def score_aggregate(d: pd.DataFrame, s: pd.DataFrame, group_col: str, label_col: str, detail_col: str,
                    summary_col: str, agg: str, rel_tol: float = 0.005,
                    min_rows: int = 3) -> tuple[float, int] | None:
    """Match rate of 'summary_col = agg(detail_col) grouped by group_col' over the summary labels.
    -> (match rate, labels compared), or None when fewer than min_rows labels can be compared."""
    grouped = d.groupby(d[group_col].astype(str))[detail_col].agg(agg)
    expected = s[label_col].astype(str).map(grouped)
    valid = expected.notna() & s[summary_col].notna()
    if valid.sum() < min_rows:
        return None
    ok = (expected - s[summary_col]).abs() <= rel_tol * s[summary_col].abs().clip(lower=1e-9)
    return float(ok[valid].mean()), int(valid.sum())


def detect_aggregate_rules(tables: dict[str, pd.DataFrame], rel_tol: float = 0.005, min_rows: int = 3,
                           min_match: float = 0.9, min_overlap: float = 0.6) -> list[AggregateRule]:
    """LLM-off fallback: try every combination. With the LLM on, the Cross-table Linker proposes the
    combinations instead and only those are scored."""
    best: dict[tuple, AggregateRule] = {}
    for dn, d in tables.items():
        for sn, s in tables.items():
            if dn == sn:
                continue
            for lc in _text_cols(s):
                for gc in _text_cols(d):
                    if _labels_problem(d, s, gc, lc, min_rows, min_overlap):
                        continue
                    for dc in d.select_dtypes("number").columns:
                        for agg in AGGS:
                            for sc in s.select_dtypes("number").columns:
                                scored = score_aggregate(d, s, gc, lc, dc, sc, agg, rel_tol, min_rows)
                                if scored is None:
                                    continue
                                rate, n = scored
                                key = (sn, sc)
                                if rate >= min_match and (key not in best or rate > best[key].match_rate):
                                    best[key] = AggregateRule(dn, sn, gc, lc, dc, sc, agg, rate, n)
    return list(best.values())


TOTAL_LABEL_RE = re.compile(r"\b(sub ?|grand )?total\b", re.I)


def is_total_label(label) -> bool:
    return bool(TOTAL_LABEL_RE.search(str(label)))


def apply_aggregate_rule(rule: AggregateRule, detail: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    """Summary cell = agg(detail values of its group). A 'Total' row is left alone: it is not a group,
    the Total-row roll-up computes it. A group with no detail rows is 0 for sum/count, blank otherwise."""
    grouped = detail.groupby(detail[rule.group_col].astype(str))[rule.detail_col].agg(rule.agg)
    out = summary.copy()
    labels = out[rule.label_col]
    mapped = labels.astype(str).map(grouped)
    empty = 0.0 if rule.agg in ("sum", "count") else float("nan")
    totals = labels.map(is_total_label).astype(bool)
    current = out[rule.summary_col] if rule.summary_col in out.columns else pd.Series(float("nan"), index=out.index)
    out[rule.summary_col] = mapped.where(mapped.notna(), empty).where(~totals, current)
    return out


def detect_scalar_aggregate(detail: pd.DataFrame, col: str, value: float, rel_tol: float = 0.005):
    """A single headline number (for example group revenue) that equals the sum or mean of a detail column."""
    for agg in ("sum", "mean"):
        if abs(getattr(detail[col], agg)() - value) <= rel_tol * max(abs(value), 1e-9):
            return agg
    return None
