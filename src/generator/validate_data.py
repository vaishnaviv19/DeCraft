# src/generator/validate_data.py
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd


def validate_dataset(tables: Dict[str, pd.DataFrame], constraints: List[Dict[str, Any]]) -> Dict[str, Any]:
    issues = []
    # check formulas
    for c in constraints:
        if c.get("kind") in ["strongly_inferred", "explicit"] and c.get("severity") == "hard":
            out = c.get("output")
            ins = c.get("inputs", [])
            if not out or len(ins) != 2:
                continue
            # find which table has all
            for tname, df in tables.items():
                if out in df.columns and ins[0] in df.columns and ins[1] in df.columns:
                    # check
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
                    sc = df[out].apply(pn)
                    sub = pd.DataFrame({"a": sa, "b": sb, "c": sc}).dropna()
                    if len(sub) == 0:
                        continue
                    tol = c.get("tolerance", 0.005)
                    err = (sub["a"] * sub["b"] - sub["c"]).abs()
                    bad = err > tol * sub["c"].abs().clip(lower=1e-9)
                    if bad.any():
                        issues.append({"code": "FORMULA_VIOLATION", "message": f"{out} != {ins[0]}*{ins[1]}", "table": tname, "repairable": True})
    return {"ok": len(issues) == 0, "issues": issues, "repairable": all(i.get("repairable", True) for i in issues)}
