# src/inference/_relationships.py
"""Primary Keys and Foreign Keys (M3.1, M3.2). Value overlap creates a Foreign Key, never a name match."""
from __future__ import annotations

import pandas as pd

from src.contracts import Cardinality, ColumnRef, DecisionStatus, Evidence, ExtractionGrade, Relationship
from src.inference._profile import IDENTIFIER_HEADER_RE, IDENTIFIER_VALUE_RE
from src.inference._verifiers import VerifyResult, classify_rule

MIN_UNIQUE_RATE = 0.98
MAX_MISSING_RATE = 0.02
MIN_OVERLAP = 0.90


def _identifier_like(column: str, values: pd.Series) -> bool:
    if IDENTIFIER_HEADER_RE.search(str(column)):
        return True
    sample = [str(v) for v in values.astype(str).unique()[:50]]
    if not sample:
        return False
    return sum(bool(IDENTIFIER_VALUE_RE.match(v)) for v in sample) / len(sample) >= 0.6


def candidate_keys(df: pd.DataFrame) -> list[str]:
    """A Primary Key: unique >= 0.98, missing <= 2% and identifier-like."""
    n = len(df)
    if n == 0:
        return []
    out = []
    for col in df.columns:
        values = df[col].dropna()
        if not len(values) or len(values) / n < 1 - MAX_MISSING_RATE:
            continue
        if values.nunique() / len(values) < MIN_UNIQUE_RATE:
            continue
        if pd.api.types.is_numeric_dtype(values) and not IDENTIFIER_HEADER_RE.search(str(col)):
            continue                                   # a measure such as Revenue is not a key
        if _identifier_like(col, values):
            out.append(str(col))
    return out


def _norm_values(values: pd.Series) -> set[str]:
    """Distinct values as comparable text: 101, 101.0 and ' 101 ' are the same key."""
    out = set()
    for v in values.unique():
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        out.add(str(v).strip())
    return out


def _compatible(child: pd.Series, parent: pd.Series) -> bool:
    child_num = pd.api.types.is_numeric_dtype(child)
    parent_num = pd.api.types.is_numeric_dtype(parent)
    return child_num == parent_num


def detect_relationships(tables: dict[str, pd.DataFrame], th,
                         grades: dict[str, ExtractionGrade] | None = None) -> list[Relationship]:
    """Every child column whose values reference a candidate key in another table. A link is graded
    by the worse extraction grade of its two tables (a doubtful table caps it, like any other rule)."""
    grades = grades or {}
    rank = {ExtractionGrade.GOOD: 0, ExtractionGrade.DOUBTFUL: 1, ExtractionGrade.BAD: 2}
    keys = {name: candidate_keys(df) for name, df in sorted(tables.items())}
    best: dict[tuple[str, str], tuple[float, Relationship]] = {}
    for child_table, df in sorted(tables.items()):
        for child_col in df.columns:
            child = df[child_col].dropna()
            if child.empty:
                continue
            child_set = _norm_values(child)
            for parent_table, parent_df in sorted(tables.items()):
                if parent_table == child_table:
                    continue
                for parent_col in keys[parent_table]:
                    parent = parent_df[parent_col].dropna()
                    if not _compatible(child, parent):
                        continue
                    overlap = len(child_set & _norm_values(parent)) / len(child_set)
                    if overlap < MIN_OVERLAP:
                        continue
                    grade = max(grades.get(child_table, ExtractionGrade.GOOD),
                                grades.get(parent_table, ExtractionGrade.GOOD), key=rank.__getitem__)
                    tier, status, _, reason = classify_rule(
                        VerifyResult(overlap, len(child), "foreign key overlap"), th, False, grade)
                    unique_rate = child.nunique() / len(child)
                    relationship = Relationship(
                        rel_id=f"{parent_table}.{parent_col}->{child_table}.{child_col}",
                        parent=ColumnRef(table_id=parent_table, column=str(parent_col)),
                        child=ColumnRef(table_id=child_table, column=str(child_col)),
                        cardinality=(Cardinality.ONE_TO_ONE if unique_rate >= MIN_UNIQUE_RATE
                                     else Cardinality.ONE_TO_MANY),
                        overlap_rate=float(overlap),
                        parent_unique_rate=float(parent.nunique() / len(parent)),
                        tier=tier, status=status,
                        evidence=[Evidence(source="stat_test",
                                           detail=f"{reason}; overlap={overlap:.2f} on {len(child)} rows")])
                    key = (child_table, str(child_col))
                    if key not in best or overlap > best[key][0]:
                        best[key] = (overlap, relationship)
    return [rel for _, rel in sorted(best.values(), key=lambda kv: kv[1].rel_id)]
