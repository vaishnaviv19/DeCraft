# src/generation/_apply.py
"""Apply the Generation Plan (M5.2-M5.5): sample roots, copy foreign keys, derive, roll up, aggregate.

How a root column is generated follows its Privacy Decision (method + Noise Budget):
  preserve          -> the source values, in order (entity name, address)
  row labels        -> kept in their order (statement line items, summary labels, one-per-row text);
                       identifying ones go through the Relabeler, so order stays and names are fake
  trend_preserving  -> the source shape scaled by one safe factor (+ small wobble): trends and ratios stay
  regenerate        -> numbers: fresh unique values in the same digit format
  everything else   -> a fitted distribution with the column's Noise Budget; numeric columns of one
                       table are sampled TOGETHER (Gaussian copula) so their rank correlations survive
Every number is rounded to its displayed decimals BEFORE formulas use it, so what the reader sees adds
up, and is kept >= min_value_distance from its source cell after that rounding. Source blanks stay
blank in the same cells (the validation 'empty cell' check allows exactly that).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np
import pandas as pd

from src.contracts import (Cardinality, Enforcement, FieldProfile, PatternKind, Relationship, Rule,
                           RuleKind, UnmetTarget)
from src.core.aggregation import AggregateRule, apply_aggregate_rule
from src.core.distance import ensure_distance
from src.core.expr import evaluate
from src.core.rows import apply_row_formulas as core_row_formulas
from src.core.rollup import rollup as rollup_values
from src.errors import HardConstraintError
from src.generation._copula import generate_correlated
from src.generation._distributions import sample_numeric
from src.generation._graph import Step
from src.generation._keys import fresh_key_values
from src.generation._tiny import jitter_cells, perturb_text_numbers, safe_level_factor, scale_cells
from src.generation._trend import generate_with_segments, generate_with_shape


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
    patterns: list = field(default_factory=list)
    unmet: list[UnmetTarget] = field(default_factory=list)
    relabeler: object | None = None        # privacy.Relabeler: fake labels for identifying text
    methods: dict[str, str] = field(default_factory=dict)            # node -> Privacy Decision method
    noise: dict[str, float | None] = field(default_factory=dict)     # node -> Noise Budget
    keep_order: set[str] = field(default_factory=set)                # nodes whose rows keep their labels
    statements: set[str] = field(default_factory=set)                # tables generated row by row
    presampled: dict[str, list] = field(default_factory=dict)        # node -> jointly sampled values
    cover: set[str] = field(default_factory=set)                     # group columns: every group kept
    no_corr: set[str] = field(default_factory=set)                   # sampled alone (privacy decision)
    time_order: dict[str, list[int]] = field(default_factory=dict)   # table -> row positions oldest first
    statement_factor: dict[str, float] = field(default_factory=dict) # table -> one safe factor


def _profile(ctx: GenerationContext, table: str, column: str) -> FieldProfile | None:
    return next((p for p in ctx.profiles[table] if p.column == column), None)


def _categorical(values: list, n: int, rng: np.random.Generator, alpha: float = 1.0) -> list[str]:
    """Laplace-smoothed resampling of a category column."""
    clean = pd.Series([str(v) for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))
                       and str(v) != ""])
    if clean.empty:
        return ["unknown"] * n
    counts = clean.value_counts()
    probs = (counts + alpha) / (counts.sum() + alpha * len(counts))
    return list(rng.choice(probs.index.to_numpy(), size=n, p=probs.to_numpy()))


def _cover_all(drawn: list, source: list, rng: np.random.Generator) -> list:
    """Make sure every source category appears at least once (a summary table needs every group)."""
    categories = sorted({str(v) for v in source})
    if len(drawn) < len(categories):
        return drawn
    out = list(drawn)
    positions = rng.permutation(len(out))[:len(categories)]
    for position, category in zip(positions, rng.permutation(categories)):
        out[int(position)] = str(category)
    return out


def _in_time_order(values: list, order: list[int] | None, generator) -> tuple[list, bool, int]:
    """Run a series generator on the PRESENT values in time order and put the results back in their
    rows; blank cells stay blank. `generator(series) -> (values, met, attempts)`."""
    order = order if order is not None and len(order) == len(values) else list(range(len(values)))
    rows = [i for i in order if values[i] is not None]
    made, met, attempts = generator([values[i] for i in rows])
    out: list = [None] * len(values)
    for i, v in zip(rows, made):
        out[i] = v
    return out, met, attempts


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and np.isnan(v)) or (isinstance(v, str) and not v.strip())


def _unpack(series) -> tuple[list, bool, int]:
    return list(series.values), series.met, series.attempts


def correlated_pair(x: list, y: list, order: list[int] | None, th, rng: np.random.Generator):
    """A detected co-movement (residual correlation) between two time series, generated together in
    time order on the rows where both are present. -> (x values, y values, met, attempts); rows where
    only one value exists get a safely scaled copy of it; blanks stay blank."""
    order = order if order is not None and len(order) == len(x) else list(range(len(x)))
    both = [i for i in order if x[i] is not None and y[i] is not None]
    result = generate_correlated([x[i] for i in both], [y[i] for i in both], th, rng, th.min_value_distance)
    out_x, out_y = [None] * len(x), [None] * len(y)
    for k, i in enumerate(both):
        out_x[i], out_y[i] = result.x[k], result.y[k]
    for values, out in ((x, out_x), (y, out_y)):
        lone = [i for i in range(len(values)) if values[i] is not None and out[i] is None]
        for i, v in zip(lone, jitter_cells([values[i] for i in lone], rng, th.jitter_low, th.jitter_high,
                                           th.min_value_distance)):
            out[i] = v
    return out_x, out_y, result.met, result.attempts


def correlated_samples(columns: dict[str, list[float | None]], n: int, rng: np.random.Generator,
                       min_distance: float, noise: dict[str, float | None], min_rows: int = 8
                       ) -> dict[str, list[float]]:
    """Sample several numeric columns of one table so their rank correlations match the source.

    Each column keeps its own fitted marginal (sample_numeric); a Gaussian copula with the source
    Spearman correlation decides which drawn value goes to which row. Columns with fewer than
    `min_rows` complete rows together are sampled independently."""
    names = list(columns)
    marginals = {c: sample_numeric(columns[c], n, rng, min_distance, noise=noise.get(c)) for c in names}
    frame = pd.DataFrame(columns)
    complete = frame.dropna()
    if len(names) < 2 or len(complete) < min_rows or complete.nunique().min() < 2:
        return marginals
    corr = complete.rank().corr().to_numpy()
    vals, vecs = np.linalg.eigh(np.nan_to_num((corr + corr.T) / 2, nan=0.0) + np.eye(len(names)) * 1e-9)
    corr = vecs @ np.diag(np.clip(vals, 1e-6, None)) @ vecs.T          # nearest valid correlation
    d = np.sqrt(np.diag(corr))
    corr = corr / np.outer(d, d)
    z = rng.multivariate_normal(np.zeros(len(names)), corr, size=n)
    out = {}
    for j, c in enumerate(names):
        drawn = np.sort(np.asarray([v for v in marginals[c] if v is not None], dtype=float))
        if len(drawn) < n:
            out[c] = marginals[c]
            continue
        ranks = np.argsort(np.argsort(z[:, j]))
        out[c] = [float(drawn[r]) for r in ranks]
    return out


def _dates(values: list, n: int, rng: np.random.Generator) -> list:
    stamps = pd.to_datetime(pd.Series([v for v in values if v is not None]), format="mixed",
                            errors="coerce").dropna()
    if stamps.empty:
        return [None] * n
    lo, hi = int(stamps.min().value), int(stamps.max().value)
    return [pd.Timestamp(int(v)).normalize() for v in rng.integers(lo, hi + 1, n)]


def _sample_column(ctx: GenerationContext, table: str, column: str, tiny: bool, th,
                   rng: np.random.Generator, trend=None, segment=None) -> None:
    frame, source = ctx.tables[table], ctx.source[table]
    profile = _profile(ctx, table, column)
    n = len(frame)
    raw = list(source[column]) if column in source.columns else [None] * n
    cleaned = [None if (isinstance(v, float) and np.isnan(v)) else v for v in raw]
    total_rows = set(ctx.total_links.get(table) or {})

    if profile is not None and profile.physical_type == "period":
        # Period labels are the time axis: keep them in place so the series stays ordered.
        frame[column] = pd.Series(list(raw), index=frame.index)
        return
    node = f"{table}.{column}"
    if ctx.methods.get(node) == "preserve":
        frame[column] = pd.Series(list(raw), index=frame.index)          # entity name / address
        return
    if profile is None or profile.physical_type not in {"integer", "float", "date"}:
        # Total Rows are structural: keep their label and never sample a leaf as a total.
        sample_rows = [i for i in range(n) if i not in total_rows and not _blank(cleaned[i])]
        if node in ctx.keep_order:
            drawn = [cleaned[i] for i in sample_rows]                    # line items stay in order
        else:
            drawn = _categorical([cleaned[i] for i in sample_rows], len(sample_rows), rng)
            if node in ctx.cover:
                drawn = _cover_all(drawn, [cleaned[i] for i in sample_rows], rng)
        values: list = [None] * n
        for position, i in enumerate(sample_rows):
            values[i] = drawn[position]
        if ctx.relabeler is not None and ctx.relabeler.wants(table, column):
            # identifying text (names, IDs, cities ...): same distribution, checked fake labels
            values = [ctx.relabeler.get(table, column, v) for v in values]
        # numbers kept as text beside labels (stacked tables) are data: fabricated in their own format
        values = perturb_text_numbers(values, rng, th.jitter_low, th.jitter_high, th.min_value_distance)
        for i in total_rows:
            values[i] = cleaned[i] if cleaned[i] not in (None, "") else "Total"
        frame[column] = pd.Series(values, index=frame.index, dtype=object)
        return
    if profile.physical_type == "date":
        dates = _dates(raw, n, rng)
        frame[column] = pd.Series([None if _blank(cleaned[i]) else dates[i] for i in range(n)],
                                  index=frame.index)
        return
    values = [None if pd.isna(v) else float(v) for v in raw]
    method, noise = ctx.methods.get(node), ctx.noise.get(node)
    present = [v for v in values if v is not None]
    id_like = method == "regenerate" and bool(present) and all(float(v).is_integer() for v in present)
    if id_like:                                       # account/phone-like numbers: fresh, same digits
        sampled = [float(v) for v in fresh_key_values(source[column], n, rng)]
    elif method == "regenerate":                      # measurements: a fresh draw with the largest noise
        sampled = sample_numeric(values, n, rng, th.min_value_distance, noise=0.6)
    elif table in ctx.statement_factor and method != "preserve":
        # statements: one factor for the WHOLE table + a small wobble, so row arithmetic and
        # year-over-year movement both survive (each line item keeps its own scale)
        sampled = scale_cells(values, ctx.statement_factor[table], rng, 0.03, th.min_value_distance)
    elif node in ctx.presampled:
        sampled = ctx.presampled[node]
    elif segment is not None:
        order = ctx.time_order.get(table)
        sampled, met, attempts = _in_time_order(values, order, lambda xs: _unpack(
            generate_with_segments(xs, segment, th, rng, th.min_value_distance)))
        shaped = SimpleNamespace(met=met, attempts=attempts)
        if not shaped.met:
            ctx.unmet.append(UnmetTarget(
                pattern_id=segment.pattern_id, target="segment shape",
                achieved="not reproduced",
                note=f"best of {shaped.attempts} attempts changed the segment directions"))
    elif trend is not None:
        order = ctx.time_order.get(table)
        sampled, met, attempts = _in_time_order(values, order, lambda xs: _unpack(
            generate_with_shape(xs, trend.direction.value, th, rng, th.min_value_distance)))
        shaped = SimpleNamespace(met=met, attempts=attempts)
        if not shaped.met:
            ctx.unmet.append(UnmetTarget(
                pattern_id=trend.pattern_id, target=f"trend {trend.direction.value}",
                achieved="not reproduced",
                note=f"best of {shaped.attempts} attempts did not keep the direction"))
    elif method == "trend_preserving" and noise:
        # a policy-only series with no detected pattern: one safe level factor plus a small wobble
        sampled = jitter_cells(values, rng, 1 - noise, 1 + noise, th.min_value_distance)
    elif tiny:
        # small tables keep each row's own scale: one safe factor per column plus a small wobble
        sampled = jitter_cells(values, rng, th.jitter_low, th.jitter_high, th.min_value_distance)
    else:
        sampled = sample_numeric(values, n, rng, th.min_value_distance, noise=noise)
    # Headline Distance (D-13): the largest value and the column total must differ >= 10% from source.
    # Statement tables are exempt: their rows are series whose shape must survive, and the per-table
    # factor already moves every total.
    leaves = [i for i in range(len(values)) if i not in total_rows and values[i] is not None]
    if leaves and table not in ctx.statements:
        sampled = headline_push(sampled, values, leaves, th.min_headline_distance, th.min_value_distance)
    # Round to the displayed decimals NOW (formulas then use exactly what is printed), keeping the
    # distance after that rounding, and leave source blanks blank.
    places = ctx.decimals.get(table, {}).get(column)
    out = []
    for i, new in enumerate(sampled):
        old = values[i] if i < len(values) else None
        if i < len(cleaned) and _blank(cleaned[i]) and i not in total_rows:
            out.append(np.nan)
            continue
        if id_like:
            out.append(new)
            continue
        moved = ensure_distance(new, old, th.min_value_distance, places)
        # Displayed distance: a scaled statement shows 'Rs cr', so two base values that differ a lot
        # can still print the same number. Move one display unit until the printed values differ.
        if moved is not None and old not in (None, 0) and profile is not None \
                and profile.scale != 1 and places is not None:
            shown_old = round(old / profile.scale, places)
            shown_new = round(moved / profile.scale, places)
            if shown_old and abs(shown_new - shown_old) / abs(shown_old) < th.min_value_distance:
                unit = 10.0 ** -places
                direction = 1.0 if moved >= old else -1.0
                target = shown_old
                for _ in range(12):
                    target = round(target + direction * max(unit, abs(shown_old)
                                                            * th.min_value_distance * 1.2), places)
                    if abs(target - shown_old) / abs(shown_old) >= th.min_value_distance:
                        break
                moved = target * profile.scale
        out.append(np.nan if moved is None else (round(moved, places) if places is not None else moved))
    frame[column] = pd.Series(out, index=frame.index, dtype="float64")


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
        first = pd.to_datetime(frame[earlier], errors="coerce")
        second = pd.to_datetime(frame[later], errors="coerce")
        bad = second < first
        if bad.any():
            gap = pd.to_timedelta(rule.params.get("min_gap_days", 1), unit="D")
            frame.loc[bad, later] = first[bad] + gap


def apply_row_formulas(ctx: GenerationContext, rules: list[Rule]) -> None:
    """Compute every HARD row formula (Gross profit = Revenue - Cost of sales); see core.rows."""
    for table in sorted({r.table_id for r in rules}):
        frame, source = ctx.tables.get(table), ctx.source.get(table)
        if frame is None or source is None:
            continue
        table_rules = [r for r in rules if r.table_id == table]
        applied, warnings = core_row_formulas(frame, source, table_rules, ctx.decimals.get(table, {}))
        ctx.warnings += [f"{table}: {w}" for w in warnings]
        for rule in table_rules:
            if rule.rule_id in applied:
                ctx.lineage[f"{table}.row:{rule.formula.output}"] = f"row_rule:{rule.rule_id}"


def _as_aggregate(rule: Rule) -> AggregateRule:
    p = rule.params
    return AggregateRule(detail=p["detail"], summary=rule.table_id, group_col=p["group_col"],
                         label_col=p["label_col"], detail_col=p["detail_col"],
                         summary_col=p["summary_col"], agg=p["agg"],
                         match_rate=rule.match_rate or 0.0, n_rows=rule.n_rows)


def apply_steps(ctx: GenerationContext, steps: list[Step], rules: list[Rule],
                rels: list[Relationship], tiny_tables: set[str], th,
                rng: np.random.Generator, patterns: list | None = None) -> None:
    """Walk the deterministic Generation Plan. Only HARD rules produced steps, so nothing is weakened."""
    trend_by_node = {f"{p.fields[0].table_id}.{p.fields[0].column}": p for p in (patterns or [])
                     if p.kind is PatternKind.TREND and p.tier == "confirmed" and not p.uncertain
                     and p.fields}
    segment_by_node = {}
    for pattern in patterns or []:
        if pattern.kind is PatternKind.SEGMENTS and pattern.tier != "uncertain" \
                and len(pattern.segments) >= 2 and pattern.fields:
            node = f"{pattern.fields[0].table_id}.{pattern.fields[0].column}"
            segment_by_node[node] = SimpleNamespace(
                pattern_id=pattern.pattern_id, segments=pattern.segments,
                directions=[s.direction for s in pattern.segments])
    formula_by_output = {(r.table_id, r.formula.output): r for r in rules
                         if r.kind is RuleKind.FORMULA and r.formula is not None}
    aggregate_by_output = {(r.table_id, r.params["summary_col"]): r for r in rules
                           if r.kind is RuleKind.AGGREGATE and "summary_col" in r.params}
    rel_by_child = {rel.child.node: rel for rel in rels}
    additive = {(p.table_id, p.column) for table in ctx.source
                for p in ctx.profiles[table] if p.additive}
    sampled_nodes = {s.node for s in steps if s.action == "sample"}
    for table in sorted(ctx.statements):                # one factor per statement, drawn once (even a
        if table in ctx.source:                         # small statement: per-column factors break trends)
            ctx.statement_factor[table] = safe_level_factor(
                rng, th.jitter_low, th.jitter_high, th.min_headline_distance * 1.2)
    paired: set[str] = set()
    for pattern in patterns or []:                      # detected co-movement: ONE correlation step,
        if pattern.kind is not PatternKind.CORRELATION or pattern.uncertain or len(pattern.fields) < 2:
            continue                                    # before formulas, totals and rounding
        first, second = pattern.fields[0], pattern.fields[1]
        table = first.table_id
        nodes = (f"{table}.{first.column}", f"{table}.{second.column}")
        if second.table_id != table or table not in ctx.source or table in tiny_tables \
                or table in ctx.statement_factor or any(n not in sampled_nodes or n in paired for n in nodes) \
                or any(ctx.methods.get(n) in ("preserve", "regenerate") for n in nodes):
            continue
        src = ctx.source[table]
        xs = [None if pd.isna(v) else float(v) for v in src[first.column]]
        ys = [None if pd.isna(v) else float(v) for v in src[second.column]]
        out_x, out_y, met, attempts = correlated_pair(xs, ys, ctx.time_order.get(table), th, rng)
        if attempts == 0:
            continue                                    # a weak relationship is not manufactured
        ctx.presampled[nodes[0]], ctx.presampled[nodes[1]] = out_x, out_y
        paired |= set(nodes)
        if not met:
            ctx.unmet.append(UnmetTarget(pattern_id=pattern.pattern_id,
                                         target=f"correlation {pattern.direction.value}",
                                         achieved="not reproduced", note=f"best of {attempts} attempts"))
    for table, source in ctx.source.items():
        if table in tiny_tables or table in ctx.statement_factor:
            continue
        group = {}
        for p in ctx.profiles[table]:
            node = f"{table}.{p.column}"
            if (node in sampled_nodes and p.physical_type in {"integer", "float"} and node not in trend_by_node
                    and node not in segment_by_node and node not in paired
                    and ctx.methods.get(node) not in ("preserve", "trend_preserving", "regenerate")
                    and node not in ctx.no_corr):
                group[p.column] = [None if pd.isna(v) else float(v) for v in source[p.column]]
        if len(group) >= 2:
            joint = correlated_samples(group, len(source), rng, th.min_value_distance,
                                       {c: ctx.noise.get(f"{table}.{c}") for c in group},
                                       getattr(th, "min_rows_corr", 8))
            ctx.presampled.update({f"{table}.{c}": v for c, v in joint.items()})

    def run(step: Step) -> None:
        table, column = step.node.split(".", 1)
        frame = ctx.tables[table]
        if step.action == "fresh_key":
            src_keys = ctx.source[table][column]
            wordy = src_keys.dropna().astype(str).str.contains(r"\d").mean() < 0.2 if src_keys.notna().any() else False
            if ctx.relabeler is not None and ctx.relabeler.wants(table, column) and wordy:
                # keys with no format to copy (names): believable fakes, one per source key
                frame[column] = [ctx.relabeler.get(table, column, v) for v in src_keys]
            else:
                frame[column] = fresh_key_values(src_keys, len(frame), rng)
            ctx.key_columns.setdefault(table, []).append(column)
            ctx.lineage[step.node] = "fresh_key"
        elif step.action == "sample":
            _sample_column(ctx, table, column, table in tiny_tables, th, rng,
                           trend=trend_by_node.get(step.node),
                           segment=segment_by_node.get(step.node))
            ctx.lineage[step.node] = "sample"
        elif step.action == "copy_fk":
            rel = rel_by_child[step.node]
            parent = ctx.tables.get(rel.parent.table_id)
            if parent is None or rel.parent.column not in parent.columns:
                raise HardConstraintError(f"{step.node}: parent key has not been generated")
            keys = parent[rel.parent.column].dropna().to_numpy()
            src_child = ctx.source[table][column] if column in ctx.source[table].columns else None
            filled = [i for i in range(len(frame))
                      if src_child is None or i >= len(src_child) or not _blank(src_child.iloc[i])]
            replace = rel.cardinality is not Cardinality.ONE_TO_ONE or len(filled) > len(keys)
            if rel.cardinality is Cardinality.ONE_TO_ONE and replace:
                ctx.warnings.append(f"{step.node}: more rows than parent keys, one-to-one not possible")
            drawn = rng.choice(keys, size=len(filled), replace=replace) if len(keys) else []
            values = [None] * len(frame)
            for position, i in enumerate(filled):
                values[i] = drawn[position]
            frame[column] = pd.Series(values, index=frame.index, dtype=object if keys.dtype == object else None)
            ctx.lineage[step.node] = f"fk:{rel.rel_id}"
        elif step.action == "derive":
            rule = formula_by_output[(table, column)]
            frame[column] = evaluate(rule.formula.expr, frame)
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
                values = evaluate(formula_by_output[(table, column)].formula.expr, ctx.tables[table])
                for total in (ctx.total_links.get(table) or {}):
                    ctx.tables[table].at[total, column] = values.get(total, np.nan)
            else:
                ctx.warnings.append(f"{step.node}: non-additive total row left blank")
                for total in (ctx.total_links.get(table) or {}):
                    ctx.tables[table].at[total, column] = np.nan
            ctx.lineage[step.node] = "rollup"
    for step in steps:
        run(step)
    row_rules = [r for r in rules if r.kind is RuleKind.ROW_FORMULA and r.formula is not None]
    for _ in range(2 if row_rules else 0):        # rows feed totals/formulas, which can feed rows again
        apply_row_formulas(ctx, row_rules)
        for step in steps:
            if step.action in ("derive", "rollup", "aggregate"):
                run(step)
    _enforce_ordering(ctx, rules)


def headline_push(sampled: list, values: list, leaves: list[int], headline: float, distance: float,
                  rounds: int = 4) -> list:
    """Headline Distance (D-13): the largest value AND the column total must each differ >= `headline`
    from the source. The two pushes are repeated until both hold, because scaling the column for the
    total can undo the push of the largest value (live run 2026-10-26: 337 scaled back to 301)."""
    sampled = list(sampled)
    source_max = max(values[i] for i in leaves)
    source_total = sum(values[i] for i in leaves)

    def far(a, b):
        return not b or abs(a - b) / abs(b) >= headline

    for _ in range(rounds):
        present = [i for i in leaves if i < len(sampled) and sampled[i] is not None]
        if not present:
            return sampled
        new_total = sum(sampled[i] for i in present)
        if not far(new_total, source_total):
            factor = 1 + headline * 1.2
            factor = 1 / factor if new_total < source_total else factor
            sampled = [None if v is None else v * factor for v in sampled]
        max_index = max(present, key=lambda i: abs(sampled[i]))
        if not far(sampled[max_index], source_max):
            own = values[max_index]
            candidates = (source_max * (1 + headline * 1.2), source_max * (1 - headline * 1.2))
            if sampled[max_index] > source_max:
                candidates = tuple(reversed(candidates))
            for candidate in candidates:
                if own in (None, 0) or abs(candidate - own) / abs(own) >= distance:
                    sampled[max_index] = candidate
                    break
        present = [i for i in leaves if i < len(sampled) and sampled[i] is not None]
        top = max(present, key=lambda i: abs(sampled[i]))
        if far(sum(sampled[i] for i in present), source_total) and far(sampled[top], source_max):
            break
    return sampled

