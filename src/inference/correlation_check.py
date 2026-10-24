# src/inference/correlation_check.py
from __future__ import annotations

from typing import Any, Dict

import pandas as pd
from scipy.stats import pearsonr, spearmanr


def numeric_cols(df: pd.DataFrame):
    cols = []
    for c in df.columns:
        try:
            s = pd.to_numeric(df[c], errors="coerce")
            if s.notna().sum() >= 3:
                cols.append(str(c))
        except Exception:
            continue
    return cols


def correlation_matrix(df: pd.DataFrame) -> Dict[str, Any]:
    num = numeric_cols(df)
    res = {"numeric_cols": num, "pearson": {}, "spearman": {}}
    for i in range(len(num)):
        for j in range(i + 1, len(num)):
            a, b = num[i], num[j]
            try:
                sa = pd.to_numeric(df[a], errors="coerce")
                sb = pd.to_numeric(df[b], errors="coerce")
                sub = pd.DataFrame({"a": sa, "b": sb}).dropna()
                if len(sub) < 3:
                    continue
                p, _ = pearsonr(sub["a"], sub["b"])
                res["pearson"][f"{a}|{b}"] = round(float(p), 4)
            except Exception:
                pass
            try:
                sa = pd.to_numeric(df[a], errors="coerce")
                sb = pd.to_numeric(df[b], errors="coerce")
                sub = pd.DataFrame({"a": sa, "b": sb}).dropna()
                if len(sub) < 3:
                    continue
                sp, _ = spearmanr(sub["a"], sub["b"])
                res["spearman"][f"{a}|{b}"] = round(float(sp), 4)
            except Exception:
                pass
    return res
