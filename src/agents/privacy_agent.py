# src/agents/privacy_agent.py
from __future__ import annotations

import json
from typing import Any, Dict

import pandas as pd

from .llm_client import LLMClient
from src.inference.correlation_check import correlation_matrix


class PrivacyAgent:
    def __init__(self, llm: LLMClient = None):
        self.llm = llm or LLMClient()

    def assess(self, tables: Dict[str, pd.DataFrame], blueprint: Dict[str, Any]) -> Dict[str, Any]:
        if not self.llm.available():
            return {"enabled": False, "per_table": {}}
        out = {"enabled": True, "per_table": {}}
        ctx = []
        for h in blueprint.get("headings", [])[:15]:
            ctx.append(h.get("text"))
        snippets = " ".join([str(b.get("text", "")) for b in blueprint.get("text_blocks", [])[:80]])
        system = """You are a privacy/obfuscation advisor for synthetic decoy documents.
Read PDF context + table schema + samples + correlations between numeric columns.
For EACH column decide:
- criticality: 0-10 (10 = directly identifying or highly sensitive/revealing)
- distortion: one of [none, jitter, scale, round, generalize, mask_partial, smear]
- strength: 0.0-1.0 (how much to distort). Higher for critical but keep plausible
- preserve_correlation: true/false (if strongly correlated to others keep relation realistic)
- invertible_risk: low|med|high
- rationale: short reason
Goal: make values look natural enough that a hacker won't be immediately suspicious, but hard to infer originals. Don't make numbers look fabricated. Preserve domain realism."""
        for tname, df in tables.items():
            try:
                corr = correlation_matrix(df)
                schema = []
                for c in df.columns:
                    schema.append({"name": str(c), "sample": df[c].head(6).astype(str).tolist(), "dtype": str(df[c].dtype)})
                prompt = f"PDF CONTEXT HEADINGS: {ctx}\nSNIPPETS: {snippets[:800]}\n\nTABLE: {tname}\nSCHEMA+SAMPLES:\n{json.dumps(schema, indent=2)}\n\nCORRELATIONS:\n{json.dumps(corr, indent=2)}\n\nReturn JSON: {{\"table\":\"{tname}\", \"columns\": [{{\"name\":\"...\",\"criticality\":...,\"distortion\":\"...\",\"strength\":...,\"preserve_correlation\":...,\"invertible_risk\":\"...\",\"rationale\":\"...\"}}]}}"
                resp = self.llm.complete(prompt, system=system)
                if not resp:
                    continue
                s = resp.find("{")
                e = resp.rfind("}") + 1
                if s >= 0 and e > s:
                    obj = json.loads(resp[s:e])
                    out["per_table"][tname] = obj
            except Exception:
                continue
        return out
