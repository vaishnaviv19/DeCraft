# src/generation/__init__.py  -- PUBLIC INTERFACE of the generation module (deep module)
"""Build the Generation Plan from HARD rules and generate the Canonical Dataset.

`generate` is the only public function. Internals: the dependency graph and plan (`_graph`), root
sampling (`_distributions`, `_tiny`), Fresh Keys (`_keys`) and the plan walker (`_apply`).
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from src.config import Config
from src.contracts import (DecisionStatus, Enforcement, PrivacyPlan, RuleKind, SCHEMA_VERSION,
                           ValidationReport)
from src.contracts.dataset import CanonicalDataset
from src.core.frames import table_frame
from src.core.rounding import build_display_df
from src.core.hierarchy import total_row_links
from src.generation._apply import GenerationContext, apply_steps
from src.generation._facts import compute_facts
from src.core.periods import time_order
from src.core.rows import statement_label_column
from src.generation._charts import register_charts
from src.generation._graph import build_plan
from src.privacy import Relabeler, document_values
from src.validation import repair_dataset, run_bounded_repair, validate

__all__ = ["generate", "register_charts"]


def _links(table_id: str, frame: pd.DataFrame, profiles: list) -> dict[int, list[int]]:
    label = next((p.column for p in profiles if p.physical_type == "text"), None)
    return total_row_links(frame, label)


def _statements(report, hard_rules, source: dict[str, pd.DataFrame], profiles) -> set[str]:
    """Tables whose rows are line items: they have HARD row formulas or look like a statement."""
    out = {r.table_id for r in hard_rules if r.kind is RuleKind.ROW_FORMULA}
    for table_id, frame in source.items():
        label = next((p.column for p in profiles.get(table_id, []) if p.physical_type == "text"), None)
        if statement_label_column(frame, label):
            out.add(table_id)
    return out


def _keep_order(report, hard_rules, source: dict[str, pd.DataFrame], key_nodes: set[str]) -> set[str]:
    """Text columns whose rows must keep their labels in order instead of being re-drawn:
    * the Annotator said so (role 'row_label': statement line items, parameter names);
    * the label column of a summary table (one row per group: re-drawing would repeat or drop groups);
    * any text column with one distinct value per row (each row is a different thing)."""
    nodes = {key for key, role in report.column_roles.items() if role == "row_label"}
    nodes |= {f"{r.table_id}.{r.params['label_col']}" for r in hard_rules
              if r.kind is RuleKind.AGGREGATE and "label_col" in r.params}
    for table_id, frame in source.items():
        for column in frame.columns:
            values = frame[column].dropna()
            node = f"{table_id}.{column}"
            if (node not in key_nodes and len(values) >= 2 and values.is_unique
                    and not pd.api.types.is_numeric_dtype(frame[column])
                    and not pd.api.types.is_datetime64_any_dtype(frame[column])):
                nodes.add(node)
    return nodes


def generate(doc, report, plan: PrivacyPlan, cfg: Config, rng: np.random.Generator) -> CanonicalDataset:
    """Build the Generation Plan from HARD rules, generate, and return the Canonical Dataset.

    Every generated value comes from Python; the only steps are sample, fresh key, copy FK, derive,
    rollup and aggregate. Running twice with the same seed returns identical tables.
    """
    th = cfg.thresholds
    profiles: dict[str, list] = defaultdict(list)
    for profile in report.profiles:
        profiles[profile.table_id].append(profile)

    source: dict[str, pd.DataFrame] = {}
    decimals: dict[str, dict[str, int]] = {}
    links: dict[str, dict[int, list[int]]] = {}
    for table in doc.tables:
        table_profiles = profiles.get(table.table_id)
        if not table_profiles:
            continue
        frame, table_decimals = table_frame(table, table_profiles)
        source[table.table_id] = frame
        decimals[table.table_id] = table_decimals
        links[table.table_id] = _links(table.table_id, frame, table_profiles)

    columns = {table_id: list(frame.columns) for table_id, frame in source.items()}
    hard_rules = [rule for rule in report.rules if rule.enforcement is Enforcement.HARD]
    rels = [rel for rel in report.relationships if rel.status is DecisionStatus.ACCEPTED]
    key_nodes = {f"{table_id}.{column}" for table_id, cols in report.key_columns.items()
                 for column in cols}
    rollup_nodes = {f"{p.table_id}.{p.column}" for p in report.profiles
                    if p.additive and links.get(p.table_id)}
    steps = build_plan(columns, hard_rules, rels, rollup_nodes, key_nodes)

    ctx = GenerationContext(
        source=source, profiles=dict(profiles), decimals=decimals, total_links=links,
        tables={table_id: pd.DataFrame(index=range(len(frame))) for table_id, frame in source.items()},
        relabeler=Relabeler(plan, document_values(doc)),
        methods={f"{d.table_id}.{d.column}": d.method for d in plan.decisions},
        noise={f"{d.table_id}.{d.column}": d.noise for d in plan.decisions},
        no_corr={f"{d.table_id}.{d.column}" for d in plan.decisions if not d.keep_correlation},
        statements=_statements(report, hard_rules, source, profiles),
        time_order={t: time_order(f[c]) for t, f in source.items()
                    for c in [next((p.column for p in profiles.get(t, [])
                                    if p.physical_type in {"period", "date"} and p.column in f.columns), None)]
                    if c is not None},
        keep_order=_keep_order(report, hard_rules, source, key_nodes),
        cover={f"{r.params['detail']}.{r.params['group_col']}" for r in hard_rules
               if r.kind is RuleKind.AGGREGATE and 'group_col' in r.params})
    apply_steps(ctx, steps, hard_rules, rels, set(plan.tiny_tables), th, rng,
                patterns=report.patterns)

    display = {}
    for table_id, frame in ctx.tables.items():
        mode = "sum_of_displayed" if any(p.additive for p in profiles[table_id]) else "true_total"
        display[table_id] = build_display_df(frame, decimals[table_id], links[table_id], mode,
                                             {p.column: p.scale for p in profiles[table_id]})

    dataset = CanonicalDataset(
        dataset_id=f"dataset.{doc.document_sha256[:8] or 'unknown'}",
        schema_version=SCHEMA_VERSION, seed=cfg.seed, tables=ctx.tables, display=display,
        key_columns=ctx.key_columns, rules=hard_rules, facts={}, charts={}, lineage=ctx.lineage,
        validation=ValidationReport(ok=True),        # replaced below by the bounded repair verdict
        source_profiles=report.profiles)

    # M7: bounded repair, signature unchanged; the verdict comes from validation after the last repair.
    dataset, dataset.validation = run_bounded_repair(
        dataset,
        lambda d: validate(d, report, cfg, source=doc),
        lambda d, r, attempt: repair_dataset(d, r, cfg, inference=report, rng=rng, frames=source),
        th.max_repairs)
    dataset.unmet_targets = list(ctx.unmet)
    dataset.validation = dataset.validation.model_copy(update={"unmet_targets": list(ctx.unmet)})
    # Facts are computed from the Display Tables, after repair, so narrative and tables agree.
    dataset.display = {
        table_id: build_display_df(frame, decimals[table_id], links[table_id],
                                   "sum_of_displayed" if any(p.additive for p in profiles[table_id])
                                   else "true_total",
                                   {p.column: p.scale for p in profiles[table_id]})   # source units
        for table_id, frame in dataset.tables.items()}
    dataset.facts = compute_facts(dataset, report.patterns)
    return dataset
