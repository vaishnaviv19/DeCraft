# src/inference/_relationships.py
"""Primary Keys and Foreign Keys (M3.1, M3.2). Value overlap creates a Foreign Key, never a name match.

score_link() is the single check for one proposed link; it is used both by the deterministic search
below (which only tries columns that point at a detected ID column) and by the Cross-table Linker
(_linker.py), which tries only the pairs the LLM proposes."""
from __future__ import annotations

import pandas as pd

from src.contracts import Cardinality, ColumnRef, Evidence, ExtractionGrade, Relationship
from src.core.periods import parse_period
from src.inference._profile import IDENTIFIER_HEADER_RE, IDENTIFIER_VALUE_RE
from src.inference._verifiers import VerifyResult, classify_rule

MIN_UNIQUE_RATE = 0.98
MAX_MISSING_RATE = 0.02
MIN_OVERLAP = 0.90
_RANK = {ExtractionGrade.GOOD: 0, ExtractionGrade.DOUBTFUL: 1, ExtractionGrade.BAD: 2}


def worse_grade(*grades: ExtractionGrade) -> ExtractionGrade:
    return max(grades, key=_RANK.__getitem__)


def _identifier_like(column: str, values: pd.Series) -> bool:
    if IDENTIFIER_HEADER_RE.search(str(column)):
        return True
    sample = [str(v) for v in values.astype(str).unique()[:50]]
    if not sample:
        return False
    return sum(bool(IDENTIFIER_VALUE_RE.match(v)) for v in sample) / len(sample) >= 0.6


def key_problem(df: pd.DataFrame, col: str, identifier_shape: bool = True) -> str | None:
    """None when `col` can be a Primary Key, else the reason it cannot. Always: unique >= 0.98 and
    missing <= 2%. With identifier_shape (the deterministic search) it must also LOOK like an ID; an
    LLM-proposed parent skips that (the model supplied the meaning) but may not hold fractions."""
    n = len(df)
    values = df[col].dropna()
    if not n or not len(values) or len(values) / n < 1 - MAX_MISSING_RATE:
        return "parent column has too many missing values"
    if values.nunique() / len(values) < MIN_UNIQUE_RATE:
        return "parent column values are not unique"
    if pd.api.types.is_datetime64_any_dtype(values):
        return "a date column is a time axis, not a key"
    sample = [str(v) for v in values.unique()[:20]]
    if sample and all(parse_period(v) is not None for v in sample):
        return "a period column is a time axis, not a key"
    numeric = pd.api.types.is_numeric_dtype(values)
    if numeric and not (values % 1 == 0).all():
        return "parent column holds fractional numbers"
    if not identifier_shape:
        return None
    if numeric and not IDENTIFIER_HEADER_RE.search(str(col)):
        return "a plain number column is not a key"      # a measure such as Revenue is not a key
    return None if _identifier_like(col, values) else "values do not look like identifiers"


def candidate_keys(df: pd.DataFrame) -> list[str]:
    """A Primary Key: unique >= 0.98, missing <= 2% and identifier-like."""
    return [str(c) for c in df.columns if key_problem(df, c) is None]


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


def score_link(tables: dict[str, pd.DataFrame], child_table: str, child_col: str, parent_table: str,
               parent_col: str, th, grade: ExtractionGrade = ExtractionGrade.GOOD,
               evidence: list[Evidence] | None = None) -> tuple[Relationship | None, str]:
    """Test one link child_table.child_col -> parent_table.parent_col on all rows.
    -> (Relationship classified by classify_rule, "") or (None, why it is not a link). The caller has
    already checked that parent_col can be a key."""
    child = tables[child_table][child_col].dropna()
    parent = tables[parent_table][parent_col].dropna()
    if child.empty or parent.empty:
        return None, "no values to compare"
    if not _compatible(child, parent):
        return None, "types differ (number vs text)"
    child_set = _norm_values(child)
    overlap = len(child_set & _norm_values(parent)) / len(child_set)
    if overlap < MIN_OVERLAP:
        return None, f"only {overlap:.0%} of child values exist in the parent (need {MIN_OVERLAP:.0%})"
    tier, status, _, reason = classify_rule(
        VerifyResult(overlap, len(child), "foreign key overlap"), th, False, grade)
    unique_rate = child.nunique() / len(child)
    return Relationship(
        rel_id=f"{parent_table}.{parent_col}->{child_table}.{child_col}",
        parent=ColumnRef(table_id=parent_table, column=str(parent_col)),
        child=ColumnRef(table_id=child_table, column=str(child_col)),
        cardinality=(Cardinality.ONE_TO_ONE if unique_rate >= MIN_UNIQUE_RATE else Cardinality.ONE_TO_MANY),
        overlap_rate=float(overlap),
        parent_unique_rate=float(parent.nunique() / len(parent)),
        tier=tier, status=status,
        evidence=[*(evidence or []),
                  Evidence(source="stat_test", detail=f"{reason}; overlap={overlap:.2f} on {len(child)} rows")]), ""


def best_per_child(*groups: list[Relationship]) -> list[Relationship]:
    """One link per child column: the highest overlap wins; on a tie the EARLIER group wins."""
    best: dict[str, Relationship] = {}
    for group in groups:
        for rel in group:
            cur = best.get(rel.child.node)
            if cur is None or rel.overlap_rate > cur.overlap_rate:
                best[rel.child.node] = rel
    return sorted(best.values(), key=lambda r: r.rel_id)


def detect_relationships(tables: dict[str, pd.DataFrame], th,
                         grades: dict[str, ExtractionGrade] | None = None) -> list[Relationship]:
    """Every child column whose values reference a candidate key in another table. A link is graded
    by the worse extraction grade of its two tables (a doubtful table caps it, like any other rule)."""
    grades = grades or {}
    keys = {name: candidate_keys(df) for name, df in sorted(tables.items())}
    found: list[Relationship] = []
    for child_table, df in sorted(tables.items()):
        for child_col in df.columns:
            for parent_table in sorted(tables):
                if parent_table == child_table:
                    continue
                for parent_col in keys[parent_table]:
                    grade = worse_grade(grades.get(child_table, ExtractionGrade.GOOD),
                                        grades.get(parent_table, ExtractionGrade.GOOD))
                    rel, _ = score_link(tables, child_table, child_col, parent_table, parent_col, th, grade)
                    if rel is not None:
                        found.append(rel)
    return best_per_child(found)
