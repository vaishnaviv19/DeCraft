# src/generation/_graph.py
"""Typed dependency graph (fixes bugs B-02 and B-03).

* Column nodes are ALWAYS qualified 'table.column', so 'Total' in two tables never collides.
* A separate table graph orders whole tables: FK parents before children, detail before summary.
* Edge kinds are kept, so formula, foreign-key, aggregate and soft statistical links are never confused.
* Row-level rules (total rows, % of total) are not column edges; they become rollup steps that run
  after the column's leaf rows exist.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from src.contracts import DecisionStatus, Enforcement, Relationship, Rule, RuleKind
from src.errors import CyclicDependencyError, HardConstraintError


@dataclass(frozen=True)
class Step:
    action: str        # 'sample' | 'fresh_key' | 'copy_fk' | 'derive' | 'aggregate' | 'rollup'
    node: str          # 'table.column'
    rule_id: str | None = None


def q(table: str, col: str) -> str:
    return f"{table}.{col}"


def _check_one_rule_per_column(g: nx.DiGraph, by_id: dict[str, Rule]) -> None:
    """A column must get its values from ONE rule. Two different HARD rules for the same column (e.g. a
    foreign key and a formula, or two different formulas) cannot both be applied, so stop instead of
    silently picking one. The same formula found twice (by the LLM and by the total-column detector)
    is not a conflict."""
    conflicts = []
    for node in g.nodes:
        ids = sorted({d["rule_id"] for _, _, d in g.in_edges(node, data=True)})
        if len(ids) < 2:
            continue
        formulas = [by_id[i].formula.expr for i in ids if i in by_id and by_id[i].formula is not None]
        if len(formulas) == len(ids) and all(f == formulas[0] for f in formulas):
            continue
        conflicts.append(f"{node} <- {', '.join(ids)}")
    if conflicts:
        raise HardConstraintError("several hard rules produce the same column: " + "; ".join(conflicts))


def build_graphs(columns: dict[str, list[str]], rules: list[Rule], rels: list[Relationship]):
    """-> (column_graph, table_graph). Only HARD rules create edges; soft/none rules never order generation."""
    g = nx.DiGraph()
    t = nx.DiGraph()
    for table, cols in columns.items():
        t.add_node(table)
        for c in cols:
            g.add_node(q(table, c), table=table)
    missing = []
    for r in rules:
        if r.enforcement != Enforcement.HARD:
            continue
        if r.kind == RuleKind.FORMULA and r.formula:
            out = q(r.table_id, r.formula.output)
            for c in r.formula.inputs:
                src = q(r.table_id, c)
                if src not in g:
                    missing.append(f"{r.rule_id}: input {src} does not exist")
                g.add_edge(src, out, kind="formula", rule_id=r.rule_id)
        elif r.kind == RuleKind.AGGREGATE:
            src = q(r.params["detail"], r.params["detail_col"])
            dst = q(r.table_id, r.params["summary_col"])
            for n in (src, dst):
                if n not in g:
                    missing.append(f"{r.rule_id}: {n} does not exist")
            g.add_edge(src, dst, kind="aggregate", rule_id=r.rule_id)
            t.add_edge(r.params["detail"], r.table_id, kind="aggregate")
    for rel in rels:
        if rel.status != DecisionStatus.ACCEPTED:      # only accepted (HARD) links order generation
            continue
        for n in (rel.parent.node, rel.child.node):
            if n not in g:
                missing.append(f"{rel.rel_id}: {n} does not exist")
        g.add_edge(rel.parent.node, rel.child.node, kind="foreign_key", rule_id=rel.rel_id)
        t.add_edge(rel.parent.table_id, rel.child.table_id, kind="foreign_key")
    if missing:
        raise HardConstraintError("missing dependencies: " + "; ".join(sorted(set(missing))))
    _check_one_rule_per_column(g, {r.rule_id: r for r in rules})
    for graph, name in ((g, "column"), (t, "table")):
        try:
            cycle = nx.find_cycle(graph)
        except nx.NetworkXNoCycle:
            continue
        raise CyclicDependencyError(
            f"{name} graph cycle: " + " -> ".join(f"{a}" for a, _ in cycle) + f" -> {cycle[0][0]}")
    return g, t


def build_plan(columns: dict[str, list[str]], rules: list[Rule], rels: list[Relationship],
               rollup_columns: set[str] = frozenset(), key_columns: set[str] = frozenset()) -> list[Step]:
    """Deterministic generation order. Ties broken alphabetically so reruns are identical."""
    g, t = build_graphs(columns, rules, rels)
    table_rank = {tb: i for i, tb in enumerate(nx.lexicographical_topological_sort(t))}
    col_order = list(nx.lexicographical_topological_sort(
        g, key=lambda n: (table_rank[g.nodes[n]["table"]], n)))
    steps: list[Step] = []
    for node in col_order:
        preds = list(g.in_edges(node, data=True))
        kinds = {d["kind"] for _, _, d in preds}
        if "foreign_key" in kinds:
            steps.append(Step("copy_fk", node, preds[0][2]["rule_id"]))
        elif "aggregate" in kinds:
            steps.append(Step("aggregate", node, preds[0][2]["rule_id"]))
        elif "formula" in kinds:
            steps.append(Step("derive", node, preds[0][2]["rule_id"]))
        elif node in key_columns:
            steps.append(Step("fresh_key", node))
        else:
            steps.append(Step("sample", node))
        if node in rollup_columns and "aggregate" not in kinds:
            steps.append(Step("rollup", node))
    return steps
