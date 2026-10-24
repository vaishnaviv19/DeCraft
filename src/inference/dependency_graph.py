# src/inference/dependency_graph.py
from __future__ import annotations

from typing import Any, Dict, List

import networkx as nx


class CyclicDependencyError(Exception):
    pass


def build_graph(constraints: List[Dict[str, Any]], relationships: List[Dict[str, Any]]) -> nx.DiGraph:
    g = nx.DiGraph()
    for c in constraints:
        out = c.get("output")
        for src in c.get("inputs", []):
            if out:
                g.add_edge(src, out)
    for r in relationships:
        g.add_edge(r["parent_key"], r["child_key"])
    try:
        order = list(nx.topological_sort(g))
        g.graph["order"] = order
    except nx.NetworkXUnfeasible:
        try:
            cycle = nx.find_cycle(g)
        except Exception:
            cycle = []
        raise CyclicDependencyError(f"Cyclic dependency detected: {cycle}")
    return g
