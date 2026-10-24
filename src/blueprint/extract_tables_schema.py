# src/blueprint/extract_tables_schema.py
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


CURRENCY_RE = re.compile(r"[\$€£₹]|Rs\.?|INR|USD")
PCT_RE = re.compile(r"%")
UNITS_RE = re.compile(r"\b(cr|crore|mn|million|bn|billion|bps|lakh)\b", re.I)
NUM_RE = re.compile(r"^[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?$")


def _infer_cell_type(val: str) -> str:
    if val is None:
        return "null"
    s = str(val).strip()
    if s == "" or s.lower() in {"-", "na", "n/a"}:
        return "empty/null"
    if PCT_RE.search(s):
        return "percentage"
    if CURRENCY_RE.search(s):
        return "currency"
    if UNITS_RE.search(s):
        return "numeric_with_units"
    s2 = s.replace(",", "")
    if s.startswith("(") and s.endswith(")"):
        s2n = s2[1:-1]
        try:
            float(s2n)
            return "numeric"
        except Exception:
            pass
    try:
        float(s2)
        if re.match(r"^[-+]?\d+$", s2):
            return "integer"
        return "float"
    except Exception:
        pass
    return "string"


def extract_table_schemas(tables_csv_dir: Path, reconstructed_manifest_path: Path | None = None) -> List[Dict[str, Any]]:
    """
    FR-A4: schema with column headers, counts, dtypes, numeric formatting (currency, %, units)
    """
    schemas = []
    csv_files = sorted(tables_csv_dir.glob("table_*.csv"))
    for idx, csv_path in enumerate(csv_files, start=1):
        try:
            df = pd.read_csv(csv_path, encoding="utf-8-sig")
        except Exception:
            try:
                df = pd.read_csv(csv_path)
            except Exception:
                continue
        headers = [str(c) for c in df.columns]
        col_count = len(headers)
        row_count = len(df)
        col_schemas = []
        for h in headers:
            try:
                series = df[h].astype(str)
            except Exception:
                series = pd.Series([""] * len(df))
            types = [_infer_cell_type(v) for v in series]
            c = Counter([t for t in types if t not in {"empty/null", "null"}])
            dom = c.most_common(1)[0][0] if c else "string"
            has_currency = any(CURRENCY_RE.search(str(v)) for v in series)
            has_pct = any(PCT_RE.search(str(v)) for v in series)
            has_units = any(UNITS_RE.search(str(v)) for v in series)
            col_schemas.append(
                {
                    "name": h,
                    "inferred_type": dom,
                    "has_currency": bool(has_currency),
                    "has_percentage": bool(has_pct),
                    "has_units": bool(has_units),
                    "sample_values": series.head(3).tolist(),
                }
            )
        schemas.append(
            {
                "table_id": f"table_{idx:03d}",
                "source_csv": str(csv_path),
                "column_headers": headers,
                "column_count": col_count,
                "row_count": row_count,
                "columns": col_schemas,
            }
        )
    return schemas
