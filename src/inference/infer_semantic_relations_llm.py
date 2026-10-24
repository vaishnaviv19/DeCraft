# src/inference/infer_semantic_relations_llm.py
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

from src.agents.schema_agent import SchemaAgent
from src.agents.llm_client import LLMClient


def _parse_dates(s: pd.Series):
    try:
        dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
        if dt.notna().mean() >= 0.5:
            return dt
        dt = pd.to_datetime(s, errors="coerce")
        if dt.notna().mean() >= 0.5:
            return dt
    except Exception:
        pass
    return None


def _to_num(s: pd.Series):
    def pn(x):
        try:
            xs = str(x).replace(",", "").replace("$", "").replace("%", "").replace("Rs.", "").strip()
            if xs.startswith("(") and xs.endswith(")"):
                xs = "-" + xs[1:-1]
            return float(xs)
        except Exception:
            return np.nan
    return s.apply(pn)


def validate_proposal(df: pd.DataFrame, proposal: Dict[str, Any]) -> Dict[str, Any] | None:
    try:
        expr = proposal.get("expression", "")
        out_col = proposal.get("output")
        inputs = proposal.get("inputs", [])
        # try to evaluate common patterns
        # year(date) == year_col
        if "year(" in expr and "==" in expr:
            # find date col
            for c in df.columns:
                if str(c) in str(inputs) or str(c).lower() in expr.lower():
                    dt = _parse_dates(df[c])
                    if dt is not None:
                        dty = dt.dt.year
                        # find right side
                        # look for other numeric year col
                        for c2 in df.columns:
                            if c2 == c:
                                continue
                            nums = _to_num(df[c2]).dropna()
                            if len(nums) == 0:
                                continue
                            if ((nums >= 1900) & (nums <= 2100)).mean() >= 0.8:
                                sub = pd.DataFrame({"dty": dty, "y": _to_num(df[c2])}).dropna()
                                if len(sub) < 3:
                                    continue
                                match = (sub["dty"] == sub["y"]).mean()
                                if match >= 0.8:
                                    return {
                                        "id": f"llm_sem_{c}_{c2}_eq",
                                        "expression": f"year({c}) == {c2}",
                                        "inputs": [c],
                                        "output": c2,
                                        "kind": "strongly_inferred" if match >= 0.95 else "hypothesized",
                                        "severity": "hard" if match >= 0.95 else "soft",
                                        "tolerance": 0.0,
                                        "support_rows": int(len(sub)),
                                        "match_rate": float(match),
                                        "confidence": 0.95 if match >= 0.95 else 0.7,
                                        "evidence": [{"source": "stat_test", "detail": f"LLM proposed, validated: year({c})=={c2} match {match:.2%}", "weight": 0.95 if match >= 0.95 else 0.7}]
                                    }
        # diff: Age == REF - year(DOB)
        if "-" in expr and "year(" in expr:
            for dc in df.columns:
                dt = _parse_dates(df[dc])
                if dt is None:
                    continue
                dty = dt.dt.year
                for ac in df.columns:
                    if ac == dc:
                        continue
                    a = _to_num(df[ac]).dropna()
                    if len(a) == 0:
                        continue
                    sub = pd.DataFrame({"dty": dty, "a": _to_num(df[ac])}).dropna()
                    if len(sub) < 3:
                        continue
                    for ref in [int(sub["dty"].max()), 2025, 2026, 2024]:
                        calc = ref - sub["dty"]
                        match = (calc == sub["a"]).mean()
                        if match >= 0.8:
                            return {
                                "id": f"llm_sem_{ac}_{dc}_diff",
                                "expression": f"{ac} == {ref} - year({dc})",
                                "inputs": [dc],
                                "output": ac,
                                "kind": "strongly_inferred" if match >= 0.95 else "hypothesized",
                                "severity": "hard" if match >= 0.95 else "soft",
                                "tolerance": 0.0,
                                "support_rows": int(len(sub)),
                                "match_rate": float(match),
                                "confidence": 0.95 if match >= 0.95 else 0.7,
                                "evidence": [{"source": "stat_test", "detail": f"LLM proposed, validated: {ac} == {ref}-year({dc}) match {match:.2%}", "weight": 0.95 if match >= 0.95 else 0.7}]
                            }
    except Exception:
        pass
    return None


def infer_semantic_relations_with_llm(tables: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    agent = SchemaAgent(LLMClient())
    proposals = agent.propose_semantic_relations(tables)
    validated = []
    for p in proposals:
        tname = p.get("table")
        if not tname or tname not in tables:
            continue
        df = tables[tname]
        v = validate_proposal(df, p)
        if v:
            validated.append(v)
    return validated
