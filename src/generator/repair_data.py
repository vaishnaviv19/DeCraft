# src/generator/repair_data.py
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd


def repair_dataset(tables: Dict[str, pd.DataFrame], report: Dict[str, Any], constraints: List[Dict[str, Any]], attempt: int = 0):
    # simple repair: recompute derived
    new_tables = {k: v.copy() for k, v in tables.items()}
    for c in constraints:
        if c.get("kind") in ["strongly_inferred", "explicit"] and c.get("severity") == "hard":
            out = c.get("output")
            ins = c.get("inputs", [])
            if not out or len(ins) != 2:
                continue
            for tname, df in new_tables.items():
                if out in df.columns and ins[0] in df.columns and ins[1] in df.columns:
                    def pn(x):
                        try:
                            xs = str(x).replace(",", "").replace("$", "").replace("%", "")
                            xs = xs.replace("Rs.", "").strip()
                            if xs.startswith("(") and xs.endswith(")"):
                                xs = "-" + xs[1:-1]
                            return float(xs)
                        except Exception:
                            return np.nan
                    sa = df[ins[0]].apply(pn)
                    sb = df[ins[1]].apply(pn)
                    # recompute
                    df[out] = (sa * sb).round(2)
                    new_tables[tname] = df
    return new_tables
