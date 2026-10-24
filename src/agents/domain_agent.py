# src/agents/domain_agent.py
from __future__ import annotations

import json
from typing import Any, Dict, List

import pandas as pd

from .llm_client import LLMClient


class DomainAgent:
    def __init__(self, llm: LLMClient = None):
        self.llm = llm or LLMClient()

    def infer_domain(self, tables: Dict[str, pd.DataFrame], headings: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.llm.available():
            return {"label": "unknown", "confidence": 0.0, "evidence": []}
        summary = []
        for tname, df in tables.items():
            summary.append({"table": tname, "columns": [str(c) for c in df.columns], "rows": len(df)})
        h = []
        if headings:
            for x in headings[:10]:
                h.append(x.get("text"))
        system = """You are a domain classification agent. Given table schemas, column names, and headings, infer the business domain.
Return JSON: {"label": "finance|healthcare|retail|hr|education|energy|logistics|other|unknown", "confidence": 0.0-1.0, "rationale": "...", "evidence": [{"source": "header", "detail": "...", "weight": 0.5}]}"""
        prompt = f"Headings: {h}\nTables: {json.dumps(summary, indent=2)}\n\nInfer domain."
        resp = self.llm.complete(prompt, system=system)
        if not resp:
            return {"label": "unknown", "confidence": 0.0, "evidence": []}
        try:
            start = resp.find('{')
            end = resp.rfind('}') + 1
            if start >= 0 and end > start:
                obj = json.loads(resp[start:end])
                obj["_source"] = "llm"
                return obj
        except Exception:
            pass
        return {"label": "unknown", "confidence": 0.0, "evidence": []}
