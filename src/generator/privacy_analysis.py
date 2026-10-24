# src/generator/privacy_analysis.py
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

try:
    from src.agents.privacy_agent import PrivacyAgent
    from src.agents.llm_client import LLMClient
except Exception:
    PrivacyAgent = None
    LLMClient = None


def analyze_privacy(tables: Dict[str, pd.DataFrame], entity_name: str = None, blueprint: Dict[str, Any] = None) -> List[Dict[str, Any]]:
    decisions = []
    llm_adv = {}
    if blueprint is not None and PrivacyAgent is not None and LLMClient is not None:
        try:
            llm_adv = PrivacyAgent(LLMClient()).assess(tables, blueprint)
        except Exception:
            llm_adv = {}
    adv_map = {}
    for tname, obj in llm_adv.get("per_table", {}).items():
        for c in obj.get("columns", []) or []:
            name = c.get("name")
            if name is not None:
                adv_map[(tname, str(name))] = c
    for tname, df in tables.items():
        for col in df.columns:
            c = str(col).lower()
            key = (tname, str(col))
            adv = adv_map.get(key)
            if adv:
                decisions.append({
                    "field": str(col),
                    "table": tname,
                    "risk": "llm_advised",
                    "method": adv.get("distortion", "jitter"),
                    "rationale": adv.get("rationale", "llm advice"),
                    "preserve": adv.get("preserve_correlation", False),
                    "criticality": adv.get("criticality", 5),
                    "strength": adv.get("strength", 0.3),
                    "advise": adv,
                })
                continue
            if c.endswith("id") or "email" in c or "phone" in c or ("name" in c and "entity" not in c and "company" not in c):
                if entity_name and str(col).lower() in ["entity", "company", "brand"]:
                    decisions.append({"field": str(col), "table": tname, "risk": "low", "method": "derived", "rationale": "preserve branding/entity", "preserve": True})
                    continue
                decisions.append({"field": str(col), "table": tname, "risk": "direct_id", "method": "fresh_id", "rationale": "direct identifier regenerated", "preserve": False})
            else:
                decisions.append({"field": str(col), "table": tname, "risk": "low", "method": "numeric_distribution", "rationale": "default", "preserve": False})
    return decisions
