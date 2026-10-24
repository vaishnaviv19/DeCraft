# src/agents/relationship_agent.py
from __future__ import annotations

import json
from typing import Any, Dict, List

import pandas as pd

from .llm_client import LLMClient


class RelationshipAgent:
    def __init__(self, llm: LLMClient = None):
        self.llm = llm or LLMClient()

    def propose_cross_table(self, tables: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
        if not self.llm.available():
            return []
        proposals = []
        # summarize all tables
        summary = []
        for tname, df in tables.items():
            schema = []
            for c in df.columns:
                sample = df[c].head(3).astype(str).tolist()
                schema.append({"name": str(c), "sample": sample})
            summary.append({"table": tname, "columns": schema})
        system = """You are a data relationship agent. Find relationships between tables and between columns across tables.
Suggest foreign keys, semantic links (same entity), or derived relations. Return JSON array:
[{"parent_table": "...", "parent_key": "...", "child_table": "...", "child_key": "...", "type": "fk|semantic", "confidence": 0.8, "rationale": "..."}]
Only suggest plausible ones."""
        prompt = f"All tables:\n{json.dumps(summary, indent=2)}\n\nPropose relationships."
        resp = self.llm.complete(prompt, system=system)
        if not resp:
            return []
        try:
            start = resp.find('[')
            end = resp.rfind(']') + 1
            if start >= 0 and end > start:
                arr = json.loads(resp[start:end])
                for p in arr:
                    p["_source"] = "llm"
                    proposals.append(p)
        except Exception:
            pass
        return proposals
