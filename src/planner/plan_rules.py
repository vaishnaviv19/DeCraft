# src/planner/plan_rules.py
from __future__ import annotations

from typing import Any, Dict


def enforce_rules(plan: Dict[str, Any], blueprint: Dict[str, Any] | None = None) -> None:
    sections = plan.get("sections", [])
    # Check FR-B4: every table must have paired chart spec
    for s in sections:
        tables = s.get("tables", [])
        reqs = s.get("paired_chart_requirements", []) or []
        for t in tables:
            tid = t.get("table_id")
            if not tid:
                continue
            has = any((r.get("table_id") == tid) for r in reqs)
            if not has:
                raise RuntimeError(f"FR-B4 violated: table {tid} in {s.get('section_id')} missing paired chart spec")
    # FR-B2 basic check - just ensure titles are set and not empty
    for s in sections:
        if not s.get("title"):
            raise RuntimeError("FR-B2/FR-B1 violated: section missing title")
    # FR-B1 check - required fields
    for s in sections:
        for field in ["section_id", "title", "purpose", "target_word_count", "required_components"]:
            if field not in s:
                raise RuntimeError(f"FR-B1 violated: missing {field} in section")
    # If blueprint provided, enforce same number of tables
    if blueprint is not None:
        orig_tables = blueprint.get("tables", []) or []
        plan_tables = []
        for s in sections:
            plan_tables.extend(s.get("tables", []) or [])
        if len(plan_tables) != len(orig_tables):
            raise RuntimeError(
                f"Table count mismatch: plan has {len(plan_tables)} tables but original has {len(orig_tables)} tables. Must be same number."
            )
