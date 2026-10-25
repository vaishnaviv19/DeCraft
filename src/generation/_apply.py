# src/generation/_apply.py
"""Apply the Generation Plan (M5.2-M5.5): sample roots, copy foreign keys, derive, roll up, aggregate."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.contracts import (Cardinality, Enforcement, Relationship, Rule, RuleKind, SourceTable,
                           FieldProfile)
from src.core.aggregation import apply_aggregate_rule
from src.core.hierarchy import verify_links
from src.core.rollup import rollup as rollup_values
from src.errors import HardConstraintError
from src.generation._distributions import sample_numeric
from src.generation._graph import Step
from src.generation._keys import fresh_key_values
from src.generation._tiny import jitter_cells


@dataclass
class GenerationContext:
    source: dict[str, pd.DataFrame]
    profiles: dict[str, list[FieldProfile]]
    decimals: dict[str, dict[str, int]]
    total_links: dict[str, dict[int, list[int]]]
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    lineage: dict[str, str] = field(default_factory=dict)
    key_columns: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _profile(ctx: GenerationContext, table: str, column: str) -> FieldProfile | None:
    return next((p for p in ctx.profiles[table] if p.column == column), None)


def _categorical(values: list[str], n: int, rng: np.random.Generator, alpha: float = 1.0) -> list[str]:
    """Laplace-smoothed resampling of a category column."""
    clean = pd.Series([v for v in values if v is not None and str(v) != ""]).astype(str)
    if clean.empty:
        return ["unknown"] * n
    counts = clean.value_counts()
    probs = (counts + alpha) / (counts.sum() + alpha * len(counts))
    return list(rng.choice(probs.index.to_numpy(), size=n, p=probs.to_numpy()))


def _dates(values: list, n: int, rng: np.random.Generator) -> list:
    stamps = pd.to_datetime(pd.Series([v for v in values if v is not None]), format="mixed",
                            errors="coerce").dropna()
    if stamps.empty:
        return [None] * n
    lo, hi = int(stamps.min().value), int(stamps.max().value)
    return [pd.Timestamp(int(v)).normalize() for v in rng.integers(lo, hi + 1, n)]


def _sample_column(ctx: GenerationContext, table: str, column: str, tiny: bool,
                   th, rng: np.random.Generator) -> None:
    frame, source = ctx.tables[table], ctx.source[table]
    profile = _profile(ctx, table, column)
    n = len(frame)
    source_values = list(source[column]) if column in source.columns else [None] * n
    if profile is None:
        frame[column] = _categorical([str(v) for v in source_values], n, rng)
        return
    if profile.physical_type in {"integer", "float"}:
        if tiny:
            values = jitter_cells([None if pd.isna(v) else float(v) for v in source_values], rng,
                                  th.jitter_low, th.jitter_high, th.min_value_distance)
        else:
            values = sample_numeric([None if pd.isna(v) else float(v) for v in source_values], n,
                                    rng, th.min_value_distance)
        frame[column] = pd.Series(values, index=frame.index, dtype="float64")
        return
    if profile.physical_type == "date":
        frame[column] = pd.Series(_dates(source_values, n, rng), index=frame.index)
        return
    frame[column] = pd.Series(_categorical([str(v) for v in source_values], n, rng),
                              index=frame.index, dtype=object)


def _rollup_column(ctx: GenerationContext, table: str, column: str) -> None:
    links = ctx.total_links.get(table) or {}
    if not links:
        return
    frame = ctx.tables[table]
    leaves = {i: float(frame.at[i, column]) for i in range(len(frame))
              if i not in links and pd.notna(frame.at[i, column])}
    for total, value in rollup_values(links, leaves).items():
        if total not in leaves:
            frame.at[total, column] = value


def _enforce_ordering(ctx: GenerationContext, rules: list[Rule]) -> None:
    for rule in rules:
        if rule.kind is not RuleKind.ORDERING or rule.enforcement is not Enforcement.HARD:
            continue
        frame = ctx.tables.get(rule.table_id)
        if frame is None or len(rule.columns) < 2:
            continue
        earlier, later = rule.columns[0], rule.columns[1]
        if earlier not in frame.columns or later not in frame.columns:
            continue
        e = pd.to_datetime(frame[earlier], errors="coerce")
        l = pd.to_datetime(frame[later], errors="coerce")
        bad = l < e
        if bad.any():
            gap = pd.to_timedelta(rule.params.get("min_gap_days", 1), unit="D")
            frame.loc[bad, later] = e[bad] + gap


def apply_steps(ctx: GenerationContext, steps: list[Step], rules: list[Rule],
                rels: list[Relationship], tiny_tables: set[str], th,
                rng: np.random.Generator) -> None:
    """Walk the deterministic Generation Plan. Only HARD rules produced steps, so nothing is weakened."""
    formula_by_output = {(r.table_id, r.formula.output): r for r in rules
                         if r.kind is RuleKind.FORMULA and r.formula is not None}
    aggregate_by_output = {(r.table_id, r.params["summary_col"]): r for r in rules
                           if r.kind is RuleKind.AGGREGATE and "summary_col" in r.params}
    rel_by_child = {rel.child.node: rel for rel in rels}
    additive = {(p.table_id, p.column) for table in ctx.source
                for p in ctx.profiles[table] if p.additive}

    for step in steps:
        table, column = step.node.split(".", 1)
        frame = ctx.tables[table]
        if step.action == "fresh_key":
            frame[column] = fresh_key_values(ctx.source[table][column], len(frame), rng)
            ctx.key_columns.setdefault(table, []).append(column)
            ctx.lineage[step.node] = "fresh_key"
        elif step.action == "sample":
            _sample_column(ctx, table, column, table in tiny_tables, th, rng)
            ctx.lineage[step.node] = "sample"
        elif step.action == "copy_fk":
            rel = rel_by_child[step.node]
            parent_frame = ctx.tables.get(rel.parent.table_id)
            if parent_frame is None or rel.parent.column not in parent_frame:
                raise HardConstraintError(f"{step.node}: parent key has not been generated")
            keys = parent_frame[rel.parent.column].to_numpy()
            replace = rel.cardinality is not Cardinality.ONE_TO_ONE
            frame[column] = rng.choice(keys, size=len(frame), replace=replace)
            ctx.lineage[step.node] = f"fk:{rel.rel_id}"
        elif step.action == "derive":
            rule = formula_by_output[(table, column)]
            frame[column] = rule.formula and __import__("src.inference", fromlist=["x"]) or None
            ctx.lineage[step.node] = f"rule:{rule.rule_id}"
            ctx.lineage[step.node] = f"rule:{rule.rule_id}"
        elif step.action == "aggregate":
            rule = aggregate_by_output[(table, column)]
            ctx.tables[table] = apply_aggregate_rule(
                _as_aggregate(rule), ctx.tables[rule.params["detail"]], frame)
            ctx.lineage[step.node] = f"aggregate:{rule.rule_id}"
        elif step.action == "rollup":
            if (table, column) in additive:
                _rollup_column(ctx, table, column)
            elif (table, column) in formula_by_output:
                rule = formula_by_output[(table, column)]
                links = ctx.total_links.get(table) or {}
                from src.inference._formula import evaluate
                values = evaluate(rule.formula.expr, ctx.tables[table])
                for total in links:
                    ctx.tables[table].at[total, column] = values.get(total, np.nan)
            else:
                ctx.warnings.append(f"{step.node}: non-additive total row left blank")
                for total in (ctx.total_links.get(table) or {}):
                    ctx.tables[table].at[total, column] = np.nan
            ctx.lineage[step.node] = "rollup"
    _enforce_ordering(ctx, rules)


def _as_aggregate(rule: Rule):
    from src.core.aggregation import AggregateRule
    p = rule.params
    return AggregateRule(detail=p["detail"], summary=rule.table_id, group_col=p["group_col"],
                         label_col=p["label_col"], detail_col=p["detail_col"],
                         summary_col=p["summary_col"], agg=p["agg"],
                         match_rate=rule.match_rate or 0.0, n_rows=rule.n_rows)
