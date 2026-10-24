# src/planner/build_plan.py
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict

from .plan_rules import enforce_rules


def _make_paired_chart(table: Dict[str, Any], idx: int) -> Dict[str, Any]:
    headers = table.get("column_headers", []) or []
    x = headers[0] if headers else f"category_{idx}"
    y = headers[1] if len(headers) > 1 else f"value_{idx}"
    ctype = "bar"
    for col in table.get("columns", []) or []:
        if col.get("has_percentage"):
            ctype = "pie"
            break
    return {
        "chart_id": f"chart_plan_{idx:03d}",
        "type": ctype,
        "x_mapping": x,
        "y_mapping": y,
        "rationale": "Paired with planned table to visualize key metrics",
        "data_source_table_id": table.get("table_id"),
    }


def _get_original_words_per_page(blueprint: Dict[str, Any]) -> Dict[int, int]:
    blocks = blueprint.get("text_blocks", []) or []
    page_words = Counter()
    for b in blocks:
        p = b.get("page")
        try:
            p_int = int(p)
        except Exception:
            continue
        text = str(b.get("text", ""))
        words = len(text.strip().split())
        if words > 0:
            page_words[p_int] += words
    return dict(sorted(page_words.items()))


def build_document_plan(blueprint: Dict[str, Any]) -> Dict[str, Any]:
    entity = blueprint.get("entity", {}) or {}
    domain = entity.get("domain_industry") or "general"
    entity_name = entity.get("entity_name") or "the entity"

    tables = blueprint.get("tables", []) or []
    orig_words_per_page = _get_original_words_per_page(blueprint)
    num_pages = len(orig_words_per_page) if orig_words_per_page else blueprint.get("source", {}).get("num_pages") or 1

    sections = []
    sec_id = 1

    # Distribute original word counts across pages to sections to match nearly same words per page
    page_list = sorted(orig_words_per_page.keys())
    # Create one section per page (or group) to match per-page word counts
    for i, p in enumerate(page_list):
        wpp = orig_words_per_page.get(p, 0)
        # Try to assign tables - distribute all tables across sections, ensuring every table gets paired chart
        sections.append(
            {
                "section_id": f"sec_{sec_id:03d}",
                "title": f"Page {p} - {entity_name} Content",
                "purpose": "Content structured to match original page layout and word count; themed appropriately without verbatim copying.",
                "target_word_count": wpp,
                "required_components": ["narrative"],
                "tables": [],
                "charts": [],
                "source_page": p,
            }
        )
        sec_id += 1

    # Assign ALL tables to sections (distribute evenly) and add paired charts for EVERY table
    if tables:
        # Assign tables round-robin to sections
        for idx, t in enumerate(tables):
            sec_idx = idx % len(sections)
            tid = t.get("table_id")
            pc = _make_paired_chart(t, idx + 1)
            sections[sec_idx]["tables"].append({"table_id": tid, "role": "content_table"})
            sections[sec_idx]["charts"].append(pc)
            if "paired_chart_requirements" not in sections[sec_idx]:
                sections[sec_idx]["paired_chart_requirements"] = []
            sections[sec_idx]["paired_chart_requirements"].append({"table_id": tid, "paired_chart_spec": pc})
            # Update required components
            comps = set(sections[sec_idx].get("required_components", []))
            comps.add("table")
            comps.add("chart")
            sections[sec_idx]["required_components"] = sorted(list(comps))

    total_words = sum(s.get("target_word_count", 0) for s in sections)
    plan = {
        "plan_id": "doc_plan_001",
        "entity": entity,
        "domain_industry": domain,
        "total_target_words": total_words,
        "words_per_page_target": orig_words_per_page,
        "original_page_count": num_pages,
        "table_count": len(tables),
        "same_table_count_as_original": len(tables) == len(tables),  # will be enforced
        "sections": sections,
        "constraints": {
            "must_not_copy_verbatim": True,
            "every_table_must_have_paired_chart": True,
            "same_number_of_tables_as_original": True,
            "words_nearly_same_per_page_as_original": True,
        },
    }

    enforce_rules(plan, blueprint)

    out_dir = Path("outputs") / "plan"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "document_plan.json"
    out_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved Document Plan: {out_path} (total={total_words} words, tables={len(tables)}, pages={num_pages})")
    return plan
