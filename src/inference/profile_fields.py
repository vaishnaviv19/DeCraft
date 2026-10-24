# src/inference/profile_fields.py
from __future__ import annotations

import re
from typing import Any, Dict, List

import pandas as pd


CURRENCY_RE = re.compile(r"[\$€£₹]|Rs\.?|INR|USD")
PCT_RE = re.compile(r"%")


def profile_column(series: pd.Series, table_name: str, col_name: str) -> Dict[str, Any]:
    s = series.dropna()
    n = len(series)
    missing_rate = 1.0 - (len(s) / n) if n > 0 else 1.0
    n_unique = s.nunique() if len(s) > 0 else 0
    # physical type
    physical_type = str(series.dtype)
    # try numeric
    minv = maxv = None
    unit = None
    fmt = None
    # check formats
    sample_strs = series.astype(str).head(20)
    if any(CURRENCY_RE.search(x) for x in sample_strs):
        unit = "USD"
        fmt = "$"
    if any(PCT_RE.search(x) for x in sample_strs):
        unit = "%"
        fmt = "%"
    # try to parse numbers
    def parse_num(x):
        if pd.isna(x):
            return None
        xs = str(x).strip()
        xs2 = xs.replace(",", "")
        xs2 = xs2.replace("$", "").replace("€", "").replace("£", "").replace("₹", "")
        xs2 = xs2.replace("Rs.", "").replace("INR", "").replace("USD", "")
        xs2 = xs2.replace("%", "")
        if xs.startswith("(") and xs.endswith(")"):
            xs2 = "-" + xs2[1:-1]
        try:
            # int if no dot
            if re.match(r"^-?\d+$", xs2):
                return float(int(xs2))
            return float(xs2)
        except Exception:
            return None

    nums = [parse_num(x) for x in series]
    nums_clean = [x for x in nums if x is not None]
    if nums_clean:
        physical_type = "float"
        minv = min(nums_clean)
        maxv = max(nums_clean)
    # semantic type heuristic
    semantic_type = "string"
    if unit == "%":
        semantic_type = "percentage"
    elif unit == "USD" or fmt == "$":
        semantic_type = "currency"
    elif nums_clean and n_unique == n and n > 0:
        semantic_type = "identifier"
    elif nums_clean:
        semantic_type = "numeric"
    evidence = []
    evidence.append({"source": "header", "detail": f"column {col_name}", "weight": 0.5})
    if unit or fmt:
        evidence.append({"source": "cell_format", "detail": f"unit={unit} fmt={fmt}", "weight": 0.6})
    confidence = 0.8 if evidence else 0.5
    if n_unique / n < 0.5 and semantic_type == "identifier":
        confidence = 0.6
    if len(evidence) >= 2:
        confidence = min(0.95, confidence + 0.1)
    return {
        "table": table_name,
        "name": col_name,
        "physical_type": physical_type,
        "semantic_type": semantic_type,
        "n": n,
        "missing_rate": round(missing_rate, 4),
        "n_unique": n_unique,
        "min": minv,
        "max": maxv,
        "unit": unit,
        "fmt": fmt,
        "confidence": confidence,
        "evidence": evidence,
    }


def profile_table(df: pd.DataFrame, table_name: str) -> List[Dict[str, Any]]:
    out = []
    for col in df.columns:
        out.append(profile_column(df[col], table_name, str(col)))
    return out
