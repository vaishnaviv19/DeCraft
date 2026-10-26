# src/validation/_checks.py
"""One function per validation stage (M7.1). Each returns Violations; messages never contain raw values."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.contracts import (DecisionStatus, Enforcement, PatternKind, RuleKind, Severity, Violation)
from src.contracts.dataset import CanonicalDataset
from src.core.distance import value_distance_violations
from src.core.expr import evaluate
from src.core.frames import table_frame
from src.core.aggregation import is_total_label
from src.core.hierarchy import total_row_links
from src.core.periods import time_order
from src.core.rows import label_positions, norm_label, number_columns, sideways
from src.core.stats import classify_trend
from src.validation._utility import category_share_warnings, range_warnings


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _profiles(report, table_id: str) -> list:
    return [p for p in report.profiles if p.table_id == table_id]


def source_frames(doc, report) -> dict[str, pd.DataFrame]:
    """The source tables as normalised frames, for distance and utility checks."""
    frames = {}
    for table in doc.tables:
        profiles = _profiles(report, table.table_id)
        if profiles:
            frames[table.table_id] = table_frame(table, profiles)[0]
    return frames


def check_numbers(data: CanonicalDataset, frames: dict[str, pd.DataFrame] | None = None) -> list[Violation]:
    """Infinity is always an error; an empty cell is an error unless the source cell was also empty."""
    out = []
    for table_id, frame in data.tables.items():
        source = (frames or {}).get(table_id)
        for column in frame.columns:
            if not pd.api.types.is_numeric_dtype(frame[column]):
                continue                                   # a text or mixed column is not a number
            values = frame[column].astype(float)
            bad = np.isinf(values).to_numpy()
            empty = values.isna().to_numpy()
            if source is not None and column in source.columns:
                missing_in_source = source[column].iloc[:len(frame)].isna().to_numpy()
                empty = empty & ~missing_in_source
            bad = bad | empty
            if bad.any():
                out.append(Violation(
                    check="invalid_number", severity=Severity.ERROR, table_id=table_id,
                    column=str(column), rows=[int(i) for i in np.flatnonzero(bad)],
                    message=f"{int(bad.sum())} cells are empty, NaN or infinite", repairable=True))
    return out


def check_keys(data: CanonicalDataset) -> list[Violation]:
    out = []
    for table_id, columns in data.key_columns.items():
        frame = data.tables.get(table_id)
        if frame is None:
            continue
        for column in columns:
            if column not in frame.columns:
                continue
            missing = frame[column].isna()
            if missing.any():
                out.append(Violation(
                    check="primary_key", severity=Severity.ERROR, table_id=table_id, column=column,
                    rows=[int(i) for i in np.flatnonzero(missing.to_numpy())],
                    message=f"{int(missing.sum())} key values are missing", repairable=True))
            duplicated = frame[column].duplicated(keep=False) & frame[column].notna()
            if duplicated.any():
                out.append(Violation(
                    check="primary_key", severity=Severity.ERROR, table_id=table_id, column=column,
                    rows=[int(i) for i in np.flatnonzero(duplicated.to_numpy())],
                    message=f"{int(duplicated.sum())} key values are duplicated", repairable=True))
    return out


def check_foreign_keys(data: CanonicalDataset, report) -> list[Violation]:
    out = []
    for rel in report.relationships:
        if rel.status is not DecisionStatus.ACCEPTED:
            continue
        child = data.tables.get(rel.child.table_id)
        parent = data.tables.get(rel.parent.table_id)
        if child is None or parent is None:
            continue
        if rel.child.column not in child.columns or rel.parent.column not in parent.columns:
            continue
        allowed = set(parent[rel.parent.column].dropna().astype(str))
        values = child[rel.child.column]
        bad = values.notna() & ~values.astype(str).isin(allowed)   # a blank link is not a broken link
        if bad.any():
            out.append(Violation(
                check="foreign_key", severity=Severity.ERROR, table_id=rel.child.table_id,
                column=rel.child.column, rule_id=rel.rel_id,
                rows=[int(i) for i in np.flatnonzero(bad.to_numpy())],
                message=f"{int(bad.sum())} values reference a missing parent key", repairable=True))
    return out


def check_formulas(data: CanonicalDataset, report, cfg) -> list[Violation]:
    out = []
    for rule in report.rules:
        if rule.enforcement is not Enforcement.HARD or rule.kind is not RuleKind.FORMULA \
                or rule.formula is None:
            continue
        frame = data.tables.get(rule.table_id)
        if frame is None or rule.formula.output not in frame.columns:
            continue
        calculated = evaluate(rule.formula.expr, frame)
        actual = _numeric(frame[rule.formula.output])
        decimals = next((p.decimals for p in _profiles(report, rule.table_id)
                         if p.column == rule.formula.output and p.decimals is not None), 6)
        tolerance = np.maximum(cfg.thresholds.rel_tol_default * actual.abs(), 0.5 * 10.0 ** -decimals)
        bad = ~((calculated.astype(float) - actual).abs() <= tolerance)
        bad = bad & ~(calculated.isna() & actual.isna())           # blank inputs -> blank output is fine
        if bad.any():
            out.append(Violation(
                check="formula", severity=Severity.ERROR, table_id=rule.table_id,
                column=rule.formula.output, rule_id=rule.rule_id,
                rows=[int(i) for i in np.flatnonzero(bad.to_numpy())],
                message=f"{int(bad.sum())} rows do not satisfy the formula", repairable=True))
    return out


def row_rule_outputs(report, table_id: str, source: pd.DataFrame) -> set[int]:
    """Row positions computed by HARD row formulas (e.g. Gross profit): derived, so no distance check."""
    out = set()
    for rule in report.rules:
        if rule.kind is not RuleKind.ROW_FORMULA or rule.enforcement is not Enforcement.HARD                 or rule.table_id != table_id or rule.formula is None:
            continue
        label = rule.params.get("label_column") or (rule.columns[0] if rule.columns else None)
        if label in source.columns:
            out |= set(label_positions(source[label]).get(norm_label(rule.formula.output), []))
    return out


def check_row_formulas(data: CanonicalDataset, report, cfg, frames: dict[str, pd.DataFrame]) -> list[Violation]:
    """Every HARD row formula holds in every number column of the generated table."""
    out = []
    for rule in report.rules:
        if rule.kind is not RuleKind.ROW_FORMULA or rule.enforcement is not Enforcement.HARD or rule.formula is None:
            continue
        frame, source = data.tables.get(rule.table_id), (frames or {}).get(rule.table_id)
        label = rule.params.get("label_column") or (rule.columns[0] if rule.columns else None)
        if frame is None or source is None or label not in source.columns:
            continue
        where = label_positions(source[label])
        labels = [rule.formula.output, *rule.formula.inputs]
        if any(len(where.get(norm_label(x), [])) != 1 for x in labels):
            continue
        positions = {x: where[norm_label(x)][0] for x in labels}
        columns = number_columns(frame, label)
        calculated = evaluate(rule.formula.expr, sideways(frame, positions, columns))
        actual = pd.Series([frame[c].iloc[positions[rule.formula.output]] for c in columns], dtype=float)
        decimals = max([p.decimals or 0 for p in _profiles(report, rule.table_id) if p.column in columns] or [0])
        tolerance = np.maximum(cfg.thresholds.rel_tol_default * actual.abs(), 0.5 * 10.0 ** -decimals * len(labels))
        bad = ~((calculated - actual).abs() <= tolerance) & ~(calculated.isna() & actual.isna())
        if bad.any():
            out.append(Violation(
                check="row_formula", severity=Severity.ERROR, table_id=rule.table_id, rule_id=rule.rule_id,
                rows=[positions[rule.formula.output]],
                message=f"{rule.formula.text} fails in {int(bad.sum())} number columns", repairable=True))
    return out


def check_ordering(data: CanonicalDataset, report) -> list[Violation]:
    out = []
    for rule in report.rules:
        if rule.enforcement is not Enforcement.HARD or rule.kind is not RuleKind.ORDERING \
                or len(rule.columns) < 2:
            continue
        frame = data.tables.get(rule.table_id)
        if frame is None or any(c not in frame.columns for c in rule.columns[:2]):
            continue
        earlier = pd.to_datetime(frame[rule.columns[0]], errors="coerce")
        later = pd.to_datetime(frame[rule.columns[1]], errors="coerce")
        bad = (later < earlier).fillna(False)
        if bad.any():
            out.append(Violation(
                check="ordering", severity=Severity.ERROR, table_id=rule.table_id,
                column=rule.columns[1], rule_id=rule.rule_id,
                rows=[int(i) for i in np.flatnonzero(bad.to_numpy())],
                message=f"{int(bad.sum())} rows break the ordering rule", repairable=True))
    return out


def check_totals(data: CanonicalDataset, report) -> list[Violation]:
    out = []
    for table_id, frame in data.tables.items():
        profiles = _profiles(report, table_id)
        label = next((p.column for p in profiles if p.physical_type == "text"), None)
        links = total_row_links(frame, label)
        if not links:
            continue
        for profile in profiles:
            if not profile.additive or profile.column not in frame.columns:
                continue
            values = [None if pd.isna(v) else float(v) for v in frame[profile.column]]
            bad = []
            for total, kids in links.items():
                if values[total] is None or not kids or any(values[k] is None for k in kids):
                    continue
                if abs(sum(values[k] for k in kids) - values[total]) > 1e-6 * max(abs(values[total]), 1e-9):
                    bad.append(total)
            if bad:
                out.append(Violation(
                    check="total_link", severity=Severity.ERROR, table_id=table_id,
                    column=profile.column, rows=bad,
                    message=f"{len(bad)} Total Rows are not the sum of their leaves", repairable=True))
    return out


def check_aggregates(data: CanonicalDataset, report) -> list[Violation]:
    out = []
    for rule in report.rules:
        if rule.enforcement is not Enforcement.HARD or rule.kind is not RuleKind.AGGREGATE:
            continue
        params = rule.params
        detail = data.tables.get(params.get("detail"))
        summary = data.tables.get(rule.table_id)
        if detail is None or summary is None:
            continue
        needed = (params["detail_col"], params["group_col"], params["label_col"], params["summary_col"])
        if params["detail_col"] not in detail.columns or params["group_col"] not in detail.columns:
            continue
        if params["label_col"] not in summary.columns or params["summary_col"] not in summary.columns:
            continue
        grouped = detail.groupby(detail[params["group_col"]].astype(str))[params["detail_col"]].agg(params["agg"])
        labels = summary[params["label_col"]]
        expected = labels.astype(str).map(grouped)
        if params["agg"] in ("sum", "count"):
            expected = expected.fillna(0.0)                  # a group with no detail rows sums to 0
        actual = _numeric(summary[params["summary_col"]])
        bad = ~np.isclose(expected.astype(float).to_numpy(), actual.to_numpy(), rtol=1e-6, atol=1e-9,
                          equal_nan=True)
        bad = bad & ~labels.map(is_total_label).astype(bool).to_numpy()   # Total rows: check_totals
        if bad.any():
            out.append(Violation(
                check="aggregate", severity=Severity.ERROR, table_id=rule.table_id,
                column=params["summary_col"], rule_id=rule.rule_id,
                rows=[int(i) for i in np.flatnonzero(bad)],
                message=f"{int(bad.sum())} summary rows differ from the detail aggregate",
                repairable=True))
    return out


def pattern_series(pattern, frame: pd.DataFrame, source: pd.DataFrame | None = None
                   ) -> tuple[list, list[int], str | None]:
    """The values of a pattern's series in TIME order -> (values, row positions, column to report).
    Column series follow the time column; statement series run along one row's period columns."""
    field = pattern.fields[0]
    if pattern.row_label:
        labels = (source if source is not None and field.column in source.columns else frame)[field.column]
        where = label_positions(labels).get(norm_label(pattern.row_label), [])
        columns = [c for c in pattern.period_columns if c in frame.columns]
        if len(where) != 1 or not columns:
            return [], [], None
        return [frame[c].iloc[where[0]] for c in columns], [where[0]], field.column
    if field.column not in frame.columns:
        return [], [], None
    time = pattern.time_field.column if pattern.time_field is not None else None
    order = time_order(frame[time]) if time and time in frame.columns else list(range(len(frame)))
    return [frame[field.column].iloc[i] for i in order], [], field.column


def check_patterns(data: CanonicalDataset, report, cfg, frames: dict[str, pd.DataFrame] | None = None
                   ) -> list[Violation]:
    """Utility Gate (D-18): a confirmed Trend direction must not flip, read in time order."""
    out = []
    for pattern in report.patterns:
        if pattern.kind is not PatternKind.TREND or pattern.tier != "confirmed" or not pattern.fields:
            continue
        field = pattern.fields[0]
        frame = data.tables.get(field.table_id)
        if frame is None:
            continue
        values, rows, column = pattern_series(pattern, frame, (frames or {}).get(field.table_id))
        if not values:
            continue
        direction = classify_trend([None if pd.isna(v) else float(v) for v in values], cfg.thresholds)[0]
        if direction in {"up", "down"} and direction != pattern.direction.value:
            out.append(Violation(
                check="utility_direction", severity=Severity.ERROR, rule_id=pattern.pattern_id,
                table_id=field.table_id, column=column, rows=rows,
                message=f"confirmed {pattern.direction.value} trend became {direction}",
                repairable=True))                            # repair re-orders the generated values
    return out


def check_source_distance(data: CanonicalDataset, report, cfg,
                          frames: dict[str, pd.DataFrame]) -> list[Violation]:
    """Value Distance for root cells that keep their row identity.

    Formula outputs, aggregate summary columns and Total Rows are derived, so they are exempt: moving
    them would break the rule that produced them.
    """
    out = []
    derived = {f"{r.table_id}.{r.formula.output}" for r in report.rules
               if r.kind is RuleKind.FORMULA and r.formula is not None}
    aggregate_columns = {(r.table_id, r.params["summary_col"]) for r in report.rules
                         if r.kind is RuleKind.AGGREGATE and "summary_col" in r.params}
    # identifiers are labels, not measurements: fresh keys and copied foreign keys are exempt
    identifiers = {f"{t}.{c}" for t, cols in data.key_columns.items() for c in cols} | {
        rel.child.node for rel in report.relationships if rel.status is DecisionStatus.ACCEPTED}
    for table_id, source in frames.items():
        synthetic = data.tables.get(table_id)
        if synthetic is None:
            continue
        if len(source) != len(synthetic):
            continue                     # the row-count rule changed the rows: no row identity
        profiles = _profiles(report, table_id)
        label = next((p.column for p in profiles if p.physical_type == "text"), None)
        total_rows = set(total_row_links(synthetic, label)) | row_rule_outputs(report, table_id, source)
        n = min(len(source), len(synthetic))
        for column in source.columns:
            if column not in synthetic.columns or not pd.api.types.is_numeric_dtype(source[column]):
                continue
            if f"{table_id}.{column}" in derived | identifiers or (table_id, str(column)) in aggregate_columns:
                continue
            generated = _numeric(synthetic[column].iloc[:n])
            original = _numeric(source[column].iloc[:n])
            close = (generated.notna() & original.notna() & (original != 0))
            ratio = (generated - original).abs() / original.abs().replace(0, np.nan)
            close = close & (ratio < cfg.thresholds.min_value_distance)
            close = np.array([bool(close.iloc[i]) and i not in total_rows for i in range(n)], dtype=bool)
            if close.any():
                out.append(Violation(
                    check="value_distance", severity=Severity.ERROR, table_id=table_id,
                    column=str(column), rows=[int(i) for i in np.flatnonzero(close)],
                    message=f"{int(close.sum())} cells within "
                            f"{cfg.thresholds.min_value_distance:.0%} of the source value",
                    repairable=True))
    return out


def check_ranges(data: CanonicalDataset, report, frames: dict[str, pd.DataFrame]) -> list[Violation]:
    out = []
    derived = {f"{r.table_id}.{r.formula.output}" for r in report.rules
               if r.kind is RuleKind.FORMULA and r.formula is not None and r.enforcement is Enforcement.HARD}
    for table_id, source in frames.items():
        synthetic = data.tables.get(table_id)
        if synthetic is None:
            continue
        row_outputs = row_rule_outputs(report, table_id, source)
        for column in source.columns:
            if column not in synthetic.columns or not pd.api.types.is_numeric_dtype(source[column]):
                continue
            if f"{table_id}.{column}" in derived:
                # a HARD rule's output follows the rule (a thin margin can become a small loss)
                if source[column].min() >= 0 and (_numeric(synthetic[column]) < 0).any():
                    out.append(Violation(
                        check="derived_negative", severity=Severity.WARNING, table_id=table_id,
                        column=str(column), message="a rule output is negative where the source never was",
                        repairable=False))
                continue
            if source[column].min() >= 0:
                bad = (_numeric(synthetic[column]) < 0).to_numpy()
                bad = bad & ~np.isin(np.arange(len(bad)), list(row_outputs))
                if bad.any():
                    out.append(Violation(
                        check="negative_value", severity=Severity.ERROR, table_id=table_id,
                        column=str(column), rows=[int(i) for i in np.flatnonzero(bad)],
                        message=f"{int(bad.sum())} negative values where the source has none",
                        repairable=True))
    return out


def check_utility(data: CanonicalDataset, report, cfg,
                  frames: dict[str, pd.DataFrame]) -> list[Violation]:
    out = []
    th = cfg.thresholds
    for table_id, source in frames.items():
        synthetic = data.tables.get(table_id)
        if synthetic is None:
            continue
        for column in source.columns:
            if column not in synthetic.columns:
                continue
            if pd.api.types.is_numeric_dtype(source[column]):
                out += range_warnings(table_id, str(column), _numeric(source[column]),
                                      _numeric(synthetic[column]), th)
            else:
                out += category_share_warnings(table_id, str(column), source[column].dropna(),
                                               synthetic[column].dropna(), th)
    return out
