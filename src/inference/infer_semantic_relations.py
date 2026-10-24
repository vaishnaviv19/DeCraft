# src/inference/infer_semantic_relations.py
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd


DATE_HINTS = ["dob", "date_of_birth", "birth", "birthdate", "dob_date", "doj", "date_of_joining", "join_date", "joining_date", "hire_date", "start_date", "end_date", "issue_date", "expiry_date"]
YEAR_HINTS = ["year", "birth_year", "yob", "yearofbirth", "joining_year", "join_year", "hire_year", "start_year"]
AGE_HINTS = ["age", "tenure", "experience", "exp", "years_of_experience"]


def _is_likely_year_series(s: pd.Series, min_rows: int = 3) -> bool:
    try:
        nums = pd.to_numeric(s, errors="coerce")
        nums = nums.dropna()
        if len(nums) < min_rows:
            return False
        ok = ((nums >= 1900) & (nums <= 2100)).mean()
        if ok >= 0.8 and nums.nunique() <= 80:
            return True
    except Exception:
        pass
    return False


def _parse_dates(s: pd.Series, min_rows: int = 3) -> pd.Series | None:
    try:
        dt = pd.to_datetime(s, errors="coerce", dayfirst=True)
        if dt.notna().sum() >= min_rows and dt.notna().mean() >= 0.6:
            return dt
        dt = pd.to_datetime(s, errors="coerce", dayfirst=False)
        if dt.notna().sum() >= min_rows and dt.notna().mean() >= 0.6:
            return dt
        # try mixed
        dt = pd.to_datetime(s, errors="coerce")
        if dt.notna().sum() >= min_rows and dt.notna().mean() >= 0.6:
            return dt
    except Exception:
        pass
    return None


def infer_semantic_relations(tables: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    constraints = []
    for tname, df in tables.items():
        cols = [str(c) for c in df.columns]
        # find date-like columns
        date_cols = []
        for c in cols:
            cl = c.lower().replace(" ", "_")
            if any(h in cl for h in DATE_HINTS) and not _is_likely_year_series(df[c]):
                dt2 = _parse_dates(df[c])
                if dt2 is not None:
                    date_cols.append((c, dt2))
        
        # find year-like
        year_cols = []
        for c in cols:
            cl = c.lower().replace(" ", "_")
            if _is_likely_year_series(df[c]) or any(h in cl for h in YEAR_HINTS):
                year_cols.append(c)
        
        # find age-like
        age_cols = []
        for c in cols:
            cl = c.lower().replace(" ", "_")
            if any(h in cl for h in AGE_HINTS):
                age_cols.append(c)
        
        # Check 1: date/dob year == birth/joining year column
        for dc, dtseries in date_cols:
            for yc in year_cols:
                try:
                    sub = pd.DataFrame({"dty": dtseries.dt.year, "y": pd.to_numeric(df[yc], errors="coerce")}).dropna()
                    if len(sub) < 3:
                        continue
                    match = (sub["dty"] == sub["y"]).mean()
                    if match >= 0.9:
                        constraints.append({
                            "id": f"sem_{tname}_{dc}_{yc}_eq",
                            "expression": f"year({dc}) == {yc}",
                            "inputs": [dc],
                            "output": yc,
                            "kind": "strongly_inferred" if match >= 0.95 else "hypothesized",
                            "severity": "hard" if match >= 0.95 else "soft",
                            "tolerance": 0.0,
                            "support_rows": int(len(sub)),
                            "match_rate": float(match),
                            "confidence": 0.95 if match >= 0.95 else 0.75,
                            "evidence": [{"source": "cross_table", "detail": f"year({dc}) matches {yc} in {match:.2%} rows", "weight": 0.95 if match >= 0.95 else 0.75}]
                        })
                except Exception:
                    continue
        
        # Check 2: age == reference_year - birth_year (or age == current_year - year(dob))
        for dc, dtseries in date_cols:
            for ac in age_cols:
                try:
                    sub = pd.DataFrame({"dty": dtseries.dt.year, "age": pd.to_numeric(df[ac], errors="coerce")}).dropna()
                    if len(sub) < 3:
                        continue
                    for ref in [int(sub["dty"].max()), 2025, 2026, 2024]:
                        calc = ref - sub["dty"]
                        match = (calc == sub["age"]).mean()
                        if match >= 0.9:
                            constraints.append({
                                "id": f"sem_{tname}_{ac}_{dc}_diff",
                                "expression": f"{ac} == {ref} - year({dc})",
                                "inputs": [dc],
                                "output": ac,
                                "kind": "strongly_inferred" if match >= 0.95 else "hypothesized",
                                "severity": "hard" if match >= 0.95 else "soft",
                                "tolerance": 0.0,
                                "support_rows": int(len(sub)),
                                "match_rate": float(match),
                                "confidence": 0.95 if match >= 0.95 else 0.7,
                                "evidence": [{"source": "cross_table", "detail": f"{ac} matches {ref} - year({dc}) in {match:.2%} rows", "weight": 0.95 if match >= 0.95 else 0.7}]
                            })
                            break
                except Exception:
                    continue
        
        # Check 3: age == birth_year_column - year(dob)? less common, also check age + year(dob) == some year
        for yc in year_cols:
            for ac in age_cols:
                try:
                    sub = pd.DataFrame({"y": pd.to_numeric(df[yc], errors="coerce"), "age": pd.to_numeric(df[ac], errors="coerce")}).dropna()
                    if len(sub) < 5:
                        continue
                    # maybe age + birth_year == current/reference year
                    ref = 2025
                    calc = sub["y"] + sub["age"]
                    match = (calc == ref).mean()
                    if match >= 0.9:  # a bit looser but still checked
                        constraints.append({
                            "id": f"sem_{tname}_{ac}_{yc}_sumref",
                            "expression": f"{ac} + {yc} == REF_YEAR",
                            "inputs": [yc],
                            "output": ac,
                            "kind": "hypothesized",
                            "severity": "soft",
                            "tolerance": 0.0,
                            "support_rows": int(len(sub)),
                            "match_rate": float(match),
                            "confidence": 0.6,
                            "evidence": [{"source": "cross_table", "detail": f"{ac}+{yc} == {ref} in {match:.2%} rows", "weight": 0.6}]
                        })
                except Exception:
                    continue
    
    return constraints
