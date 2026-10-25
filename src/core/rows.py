# src/core/rows.py
"""Row formulas (Shared Kernel): a formula between ROWS of one table, written with row labels,
e.g. "[Gross profit] = [Revenue] - [Cost of sales]". It must hold in every number column.

The formula is the ordinary Expr tree (parsed once, under the allowlist, by inference); its
"columns" are row labels. To evaluate it, the table is turned sideways: one row per number column,
one column per row label. Rows are found by POSITION in the source table, so the rule still works
when generation replaces the labels with fakes.
"""
from __future__ import annotations

import re
from graphlib import CycleError, TopologicalSorter

import pandas as pd

from src.core.expr import evaluate
from src.core.periods import parse_period

LABEL_RE = re.compile(r"\[([^\[\]]+)\]")


def norm_label(label) -> str:
    return " ".join(str(label).strip().strip(":*").split()).casefold()


def label_positions(labels: pd.Series) -> dict[str, list[int]]:
    """normalised label -> row positions (more than one position = the label repeats)."""
    out: dict[str, list[int]] = {}
    for i, v in enumerate(labels):
        if v is not None and not (isinstance(v, float) and v != v) and str(v).strip():
            out.setdefault(norm_label(v), []).append(i)
    return out


def number_columns(frame: pd.DataFrame, label_column: str) -> list[str]:
    return [c for c in frame.columns if c != label_column and pd.api.types.is_numeric_dtype(frame[c])]


def sideways(frame: pd.DataFrame, positions: dict[str, int], columns: list[str]) -> pd.DataFrame:
    """One row per number column, one column per label (the label's row position in `frame`)."""
    return pd.DataFrame({label: [frame[c].iloc[pos] for c in columns] for label, pos in positions.items()},
                        index=range(len(columns)), dtype="float64")


TOTAL_WORD_RE = re.compile(r"\btotal\b", re.I)


def statement_label_column(df: pd.DataFrame, label: str | None) -> str | None:
    """`label` when the table is a STATEMENT: line items down the side and periods (FY24, 2025, Q1)
    or a Total row across. Customer lists and the like are not statements."""
    if label is None or label not in df.columns:
        return None
    labels = [str(v).strip() for v in df[label].dropna() if str(v).strip()]
    numbers = number_columns(df, label)
    period_headers = any(parse_period(str(c)) is not None for c in numbers)
    has_total = any(TOTAL_WORD_RE.search(x) for x in labels)
    return label if numbers and len(labels) >= 3 and (period_headers or has_total) else None


def apply_row_formulas(frame: pd.DataFrame, source: pd.DataFrame, rules, decimals: dict[str, int]
                       ) -> tuple[list[str], list[str]]:
    """Compute the HARD row formulas of ONE table in every number column, inputs before outputs.
    Rows are found by their position in `source`, so fake labels do not matter; results are rounded
    to each column's displayed decimals. -> (applied rule ids, warnings). Changes `frame` in place."""
    graph = {r.rule_id: {o.rule_id for o in rules if o.formula.output in r.formula.inputs} for r in rules}
    try:
        order = list(TopologicalSorter(graph).static_order())
    except CycleError:
        return [], ["row formulas form a loop; none applied"]
    by_id = {r.rule_id: r for r in rules}
    applied, warnings = [], []
    for rule_id in order:
        rule = by_id[rule_id]
        label_column = rule.params.get("label_column") or (rule.columns[0] if rule.columns else None)
        if label_column not in source.columns:
            continue
        where = label_positions(source[label_column])
        labels = [rule.formula.output, *rule.formula.inputs]
        if any(len(where.get(norm_label(x), [])) != 1 for x in labels):
            warnings.append(f"row formula {rule.rule_id} skipped (a row is not found exactly once)")
            continue
        positions = {x: where[norm_label(x)][0] for x in labels}
        columns = number_columns(frame, label_column)
        values = evaluate(rule.formula.expr, sideways(frame, positions, columns))
        row = positions[rule.formula.output]
        for j, column in enumerate(columns):
            v = values.iloc[j]
            places = decimals.get(column)
            frame.iloc[row, frame.columns.get_loc(column)] = (
                round(float(v), places) if places is not None and pd.notna(v) else v)
        applied.append(rule.rule_id)
    return applied, warnings
