# src/inference/_quality.py
"""Three-level extraction quality (decision D-14). good -> normal inference; doubtful -> rules capped at
'hypothesized'; bad -> table skipped and logged. Pure function of the extracted strings and extractor flags."""
from __future__ import annotations

import re
from collections import Counter

from src.contracts import ExtractionGrade, SourceTable, TableQuality

NUM_RE = re.compile(
    r"^[\s(]*[-+]?(?:rs\.?\s*)?[$€£₹]?\s*[0-9][0-9,]*(\.[0-9]+)?\s*(?:%|bps|x)?[)]?\s*$", re.I)
MISSING = {"", "-", "--", "—", "–", "n/a", "na", "n.a.", "nm", "n.m."}
NUMERIC_HEADER_RE = re.compile(
    r"%|[$€£₹]|\b(rs\.?|inr|usd|revenue|sales|cost|profit|ebitda|income|expense|capex|debt|cash|"
    r"price|amount|value|units?|count|qty|quantity|margin|growth|yield|rate|total)\b", re.I)


def _numeric_like(col: list[str | None], header: str = "") -> tuple[bool, float]:
    vals = [(v or "").strip() for v in col if (v or "").strip().lower() not in MISSING]
    if not vals:
        return False, 0.0
    ok = sum(bool(NUM_RE.match(v)) for v in vals) / len(vals)
    # A column is judged as numeric when most of its values parse, or when a numeric-looking header
    # says it should and at least a fifth do parse. A plain text column is never condemned.
    looks_numeric = ok >= 0.5 or (ok >= 0.2 and bool(NUMERIC_HEADER_RE.search(header or "")))
    return looks_numeric, 1 - ok          # (column looks numeric, share of values that fail to parse)


def grade_table(t: SourceTable) -> TableQuality:
    issues_doubtful, issues_bad = [], []
    width = len(t.header)
    rows = t.raw_cells
    if not rows or width == 0:
        return TableQuality(grade=ExtractionGrade.BAD, issues=["empty table"])
    ragged = sum(len(r) != width for r in rows) / len(rows)
    if ragged > 0.10:
        issues_bad.append(f"{ragged:.0%} of rows have a different length than the header")
    elif ragged > 0:
        issues_doubtful.append("some rows have a different length than the header")
    headers = [(h or "").strip() for h in t.header]
    if all(not h for h in headers):
        issues_bad.append("all headers empty")
    elif any(not h for h in headers):
        issues_doubtful.append("empty header cell")
    dupes = [h for h, n in Counter(h for h in headers if h).items() if n > 1]
    if dupes:
        issues_doubtful.append(f"duplicated headers: {dupes}")
    for j, h in enumerate(headers):
        col = [r[j] if j < len(r) else None for r in rows]
        looks_numeric, fail = _numeric_like(col, h)
        if looks_numeric and fail > 0.5:
            issues_bad.append(f"column {h!r}: {fail:.0%} numeric parse failures")
        elif looks_numeric and fail > 0.2:
            issues_doubtful.append(f"column {h!r}: {fail:.0%} numeric parse failures")
    if any(len(r) > 1 and r[0] and all(c is None for c in r[1:]) for r in rows):
        issues_doubtful.append("possible merged cells (label-only rows with None cells)")
    if t.ocr:
        issues_doubtful.append("OCR extraction")
    if issues_bad:
        return TableQuality(grade=ExtractionGrade.BAD, issues=issues_bad + issues_doubtful)
    if issues_doubtful:
        return TableQuality(grade=ExtractionGrade.DOUBTFUL, issues=issues_doubtful)
    return TableQuality(grade=ExtractionGrade.GOOD, issues=[])


def downgrade_for_total_mismatch(q: TableQuality, total_match_rate: float | None, strong: float) -> TableQuality:
    """Called after hierarchy verification: totals that do not add up make a 'good' table 'doubtful'."""
    if q.grade == ExtractionGrade.GOOD and total_match_rate is not None and total_match_rate < strong:
        return TableQuality(grade=ExtractionGrade.DOUBTFUL,
                            issues=q.issues + ["total rows do not reconcile"])
    return q
