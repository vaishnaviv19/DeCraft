# src/planner/validate_plan.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def validate_plan(plan_path: Path) -> Dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    sections = plan.get("sections", [])
    total = sum(s.get("target_word_count", 0) for s in sections)
    issues = []
    if total < 1000:
        issues.append(f"total < 1000 ({total})")
    if total < 1500:
        issues.append(f"safety margin < 1500 ({total})")
    for s in sections:
        for t in s.get("tables", []):
            tid = t.get("table_id")
            reqs = s.get("paired_chart_requirements", [])
            if not any(r.get("table_id") == tid for r in reqs):
                issues.append(f"table {tid} missing paired chart in {s.get('section_id')}")
    return {"valid": len(issues) == 0, "issues": issues, "total_target_words": total}
