# tests/generation/test_graph.py
import pytest

from src.contracts import (Cardinality, ColumnRef, DecisionStatus, Enforcement, Relationship, Rule,
                           RuleKind, RuleTier)
from src.errors import CyclicDependencyError, HardConstraintError
from src.generation._graph import build_plan
from src.inference._formula import parse_formula


def formula(rule_id, table, out, inputs, enforcement=Enforcement.HARD):
    spec = parse_formula(f"[{out}] = " + " + ".join(f"[{c}]" for c in inputs), [out, *inputs])
    return Rule(rule_id=rule_id, kind=RuleKind.FORMULA, table_id=table, columns=[out, *inputs],
                formula=spec, tier=RuleTier.STRONGLY_INFERRED, status=DecisionStatus.ACCEPTED,
                enforcement=enforcement, reason="test")


def test_inputs_come_before_outputs():
    steps = build_plan({"t": ["a", "b", "c"]}, [formula("r1", "t", "c", ["a", "b"])], [])
    assert [(s.action, s.node) for s in steps] == [
        ("sample", "t.a"), ("sample", "t.b"), ("derive", "t.c")]


def test_same_column_name_in_two_tables_does_not_collide():
    steps = build_plan({"a": ["Total"], "b": ["Total"]}, [], [])
    assert {s.node for s in steps} == {"a.Total", "b.Total"}


def test_soft_rules_create_no_edges():
    steps = build_plan({"t": ["a", "b"]},
                       [formula("r1", "t", "b", ["a"], enforcement=Enforcement.SOFT)], [])
    assert [(s.action, s.node) for s in steps] == [("sample", "t.a"), ("sample", "t.b")]


def test_plan_is_deterministic():
    cols = {"t": ["b", "a", "c"]}
    rules = [formula("r1", "t", "c", ["a", "b"])]
    assert build_plan(cols, rules, []) == build_plan(cols, rules, [])


def test_cycle_is_reported_with_its_path():
    rules = [formula("r1", "t", "a", ["b"]), formula("r2", "t", "b", ["a"])]
    with pytest.raises(CyclicDependencyError) as e:
        build_plan({"t": ["a", "b"]}, rules, [])
    assert "t.a" in str(e.value) and "t.b" in str(e.value)


def test_missing_dependency_is_reported():
    with pytest.raises(HardConstraintError) as e:
        build_plan({"t": ["a", "c"]}, [formula("r1", "t", "c", ["a", "z"])], [])
    assert "t.z" in str(e.value)


def test_rollup_step_follows_leaf_generation():
    steps = build_plan({"t": ["a", "b", "c"]}, [formula("r1", "t", "c", ["a", "b"])], [],
                       rollup_columns={"t.c"})
    assert [(s.action, s.node) for s in steps][-2:] == [("derive", "t.c"), ("rollup", "t.c")]


def test_foreign_key_parent_table_comes_first_and_child_is_copied():
    rel = Relationship(rel_id="r", parent=ColumnRef(table_id="p", column="id"),
                       child=ColumnRef(table_id="c", column="pid"),
                       cardinality=Cardinality.ONE_TO_MANY, overlap_rate=1.0, parent_unique_rate=1.0,
                       tier=RuleTier.STRONGLY_INFERRED, status=DecisionStatus.ACCEPTED)
    steps = build_plan({"p": ["id"], "c": ["pid"]}, [], [rel])
    assert [(s.action, s.node) for s in steps] == [("sample", "p.id"), ("copy_fk", "c.pid")]


def test_aggregate_edge_orders_detail_before_summary():
    rule = Rule(rule_id="agg", kind=RuleKind.AGGREGATE, table_id="summary",
                columns=["Total Revenue"],
                params={"detail": "detail", "detail_col": "Revenue", "summary_col": "Total Revenue",
                        "group_col": "Region", "label_col": "Region", "agg": "sum"},
                tier=RuleTier.STRONGLY_INFERRED, status=DecisionStatus.ACCEPTED,
                enforcement=Enforcement.HARD, reason="test")
    steps = build_plan({"detail": ["Region", "Revenue"], "summary": ["Region", "Total Revenue"]},
                       [rule], [])
    assert ("aggregate", "summary.Total Revenue") in [(s.action, s.node) for s in steps]
    nodes = [s.node for s in steps]
    assert nodes.index("detail.Revenue") < nodes.index("summary.Total Revenue")


def _rel(status, parent=("p", "id"), child=("c", "pid")):
    return Relationship(rel_id=f"{parent[0]}->{child[0]}", parent=ColumnRef(table_id=parent[0], column=parent[1]),
                        child=ColumnRef(table_id=child[0], column=child[1]),
                        cardinality=Cardinality.ONE_TO_MANY, overlap_rate=0.92, parent_unique_rate=1.0,
                        tier=RuleTier.HYPOTHESIZED, status=status)


@pytest.mark.parametrize("status", [DecisionStatus.NEEDS_REVIEW, DecisionStatus.REJECTED])
def test_unaccepted_links_do_not_order_generation(status):
    steps = build_plan({"p": ["id"], "c": ["pid"]}, [], [_rel(status)])
    assert [(s.action, s.node) for s in steps] == [("sample", "c.pid"), ("sample", "p.id")]


def test_two_different_rules_for_one_column_are_reported():
    rules = [formula("r1", "c", "pid", ["x"])]
    with pytest.raises(HardConstraintError) as e:
        build_plan({"p": ["id"], "c": ["pid", "x"]}, rules, [_rel(DecisionStatus.ACCEPTED)])
    assert "c.pid" in str(e.value) and "r1" in str(e.value)


def test_the_same_formula_found_twice_is_not_a_conflict():
    rules = [formula("r1", "t", "c", ["a", "b"]), formula("r2", "t", "c", ["a", "b"])]
    steps = build_plan({"t": ["a", "b", "c"]}, rules, [])
    assert ("derive", "t.c") in [(s.action, s.node) for s in steps]
