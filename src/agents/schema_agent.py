# src/agents/schema_agent.py
from __future__ import annotations

import json
from typing import Any, Dict, List

import pandas as pd

from .llm_client import LLMClient


class SchemaAgent:
    def __init__(self, llm: LLMClient = None):
        self.llm = llm or LLMClient()

    def propose_semantic_relations(self, tables: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
        if not self.llm.available():
            return []
        proposals = []
        for tname, df in tables.items():
            # build schema summary
            schema = []
            for c in df.columns:
                sample = df[c].head(5).astype(str).tolist()
                schema.append({"name": str(c), "sample": sample, "dtype": str(df[c].dtype)})
            system = """You are a data inference agent. Find semantic relationships between columns.
Look for: year extracted from date equals year column (e.g., DOB/year), age/tenure equals difference between reference year and date/year.
Only propose relationships that are mathematically checkable. Return JSON array of proposals with: 
{"table": "...", "type": "equality|difference|derived", "expression": "year(DOB) == Birth_Year OR Age == 2025 - year(DOB)", "inputs": ["DOB"], "output": "Birth_Year", "rationale": "..."}
Be conservative - only propose if column names/samples suggest it."""
            prompt = f"Table: {tname}\nSchema:\n{json.dumps(schema, indent=2)}\n\nPropose semantic relations."
            resp = self.llm.complete(prompt, system=system)
            if not resp:
                continue
            try:
                # extract json
                start = resp.find('[')
                end = resp.rfind(']') + 1
                if start >= 0 and end > start:
                    arr = json.loads(resp[start:end])
                    for p in arr:
                        p["_source"] = "llm"
                        p["table"] = tname
                        proposals.append(p)
            except Exception:
                continue
        return proposals
