# src/inference/infer_relationships.py
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd


def find_relationships(tables: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    rels = []
    # try to find FK by name similarity + overlap
    names = list(tables.keys())
    for i in range(len(names)):
        for j in range(len(names)):
            if i == j:
                continue
            t1n, t2n = names[i], names[j]
            df1, df2 = tables[t1n], tables[t2n]
            for c1 in df1.columns:
                c1s = str(c1).lower()
                for c2 in df2.columns:
                    c2s = str(c2).lower()
                    # look for id patterns
                    if c1s.endswith('id') and c2s == c1s:
                        # check overlap
                        s1 = set(df1[c1].astype(str).unique())
                        s2 = set(df2[c2].astype(str).unique())
                        if not s1 or not s2:
                            continue
                        overlap = len(s1 & s2) / len(s1) if s1 else 0
                        if overlap >= 0.8:
                            rels.append({
                                "id": f"rel_{t1n}_{c1}_{t2n}_{c2}",
                                "parent_table": t2n,
                                "parent_key": str(c2),
                                "child_table": t1n,
                                "child_key": str(c1),
                                "cardinality": "one_to_many",
                                "overlap_rate": overlap,
                                "confidence": 0.9,
                                "evidence": [{"source": "cross_table", "detail": f"overlap {overlap}", "weight": 0.9}]
                            })
    return rels
