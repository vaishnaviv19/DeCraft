# src/inference/infer_constraints.py
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd


def test_product(df: pd.DataFrame, a: str, b: str, c: str, rel_tol: float = 0.005, min_rows: int = 5):
    sub = df[[a, b, c]].copy()
    sub = sub.dropna()
    if len(sub) < min_rows:
        return None
    # try to parse numbers
    def pn(x):
        try:
            xs = str(x).replace(",", "").replace("$", "").replace("%", "")
            xs = xs.replace("Rs.", "").strip()
            if xs.startswith("(") and xs.endswith(")"):
                xs = "-" + xs[1:-1]
            return float(xs)
        except Exception:
            return np.nan
    sa = sub[a].apply(pn)
    sb = sub[b].apply(pn)
    sc = sub[c].apply(pn)
    sub2 = pd.DataFrame({"a": sa, "b": sb, "c": sc}).dropna()
    if len(sub2) < min_rows:
        return None
    err = (sub2["a"] * sub2["b"] - sub2["c"]).abs()
    ok = err <= rel_tol * sub2["c"].abs().clip(lower=1e-9)
    return ok.mean(), len(sub2)


def infer_constraints(tables: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    cons = []
    for tname, df in tables.items():
        cols = list(df.columns)
        # try all triples
        for i in range(len(cols)):
            for j in range(len(cols)):
                for k in range(len(cols)):
                    if i == j or i == k or j == k:
                        continue
                    res = test_product(df, cols[i], cols[j], cols[k])
                    if res is None:
                        continue
                    match_rate, support = res
                    if match_rate >= 0.95 and support >= 5:
                        cons.append({
                            "id": f"c_{tname}_{cols[i]}_{cols[j]}_{cols[k]}",
                            "expression": f"{cols[k]} == {cols[i]} * {cols[j]}",
                            "inputs": [cols[i], cols[j]],
                            "output": cols[k],
                            "kind": "strongly_inferred",
                            "severity": "hard",
                            "tolerance": 0.005,
                            "support_rows": support,
                            "match_rate": match_rate,
                            "confidence": 0.9,
                            "evidence": [{"source": "stat_test", "detail": f"match_rate={match_rate}", "weight": 0.9}]
                        })
                    elif match_rate >= 0.80:
                        cons.append({
                            "id": f"c_{tname}_{cols[i]}_{cols[j]}_{cols[k]}_hypo",
                            "expression": f"{cols[k]} == {cols[i]} * {cols[j]}",
                            "inputs": [cols[i], cols[j]],
                            "output": cols[k],
                            "kind": "hypothesized",
                            "severity": "soft",
                            "tolerance": 0.005,
                            "support_rows": support,
                            "match_rate": match_rate,
                            "confidence": 0.6,
                            "evidence": [{"source": "stat_test", "detail": f"match_rate={match_rate}", "weight": 0.6}]
                        })
    return cons
