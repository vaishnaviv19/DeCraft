# src/core/periods.py
"""Typed time labels and the time axis (M1.4). Fiscal labels stay fiscal."""
from __future__ import annotations

import re
from dataclasses import dataclass


MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun",
                                         "jul", "aug", "sep", "oct", "nov", "dec"])}
QUARTER_RE = re.compile(r"q([1-4])[ ,/-]*(?:fy)?[ ]*([0-9]{2,4})", re.I)
MONTH_RE = re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[ ,-]*([0-9]{2,4})", re.I)
FY_RE = re.compile(r"fy[ -]?([0-9]{2,4})", re.I)
YEAR_RE = re.compile(r"(?<![0-9])((?:19|20)[0-9]{2})(?![0-9])")
FYE_RE = re.compile(r"(?:year|period)[ ]+ended?[ ]+([a-z]+)", re.I)
CUM_WORDS = ("ytd", "year to date", "year-to-date", "cumulative")
PER_YEAR = {"year": 1, "quarter": 4, "month": 12}


@dataclass(frozen=True)
class Period:
    kind: str                  # 'year' | 'quarter' | 'month'
    year: int
    sub: int = 0               # quarter 1-4 or month 1-12
    fiscal: bool = False

    @property
    def ordinal(self) -> int:
        return self.year * PER_YEAR[self.kind] + max(self.sub - 1, 0)


def _year(s: str) -> int:
    y = int(s)
    return y + 2000 if y < 100 else y


def parse_period(label: str) -> Period | None:
    t = str(label).strip().lower()
    fiscal = "fy" in t
    if m := QUARTER_RE.search(t):
        return Period("quarter", _year(m.group(2)), int(m.group(1)), fiscal)
    if m := MONTH_RE.search(t):
        return Period("month", _year(m.group(2)), MONTHS[m.group(1)[:3].lower()], fiscal)
    if m := FY_RE.search(t):
        return Period("year", _year(m.group(1)), 0, True)
    if m := YEAR_RE.search(t):
        return Period("year", int(m.group(1)), 0, False)
    return None


def fiscal_year_end_month(text: str) -> int | None:
    m = FYE_RE.search(text or "")
    return MONTHS.get(m.group(1)[:3].lower()) if m else None


def yoy_pct(levels, lag: int) -> list[float | None]:
    return [None] * lag + [100.0 * (levels[i] / levels[i - lag] - 1) if levels[i - lag] else None
                           for i in range(lag, len(levels))]


# ---------------------------------------------------------------- v3.1: period shift (decision E-16)
def shift_period(p: Period, years: int) -> Period:
    """Move a period forward by whole years. Kind, quarter/month and fiscal flag are kept."""
    return Period(p.kind, p.year + years, p.sub, p.fiscal)


def format_like(label: str, p: Period) -> str:
    """Write a shifted period in the SAME style as the source label (FY24 -> FY25, Q3 2023 -> Q3 2024)."""
    match = re.search(r"(?<!\d)(\d{4}|\d{2})(?!\d)", label)
    if not match:
        return label
    new = str(p.year) if len(match.group(1)) == 4 else f"{p.year % 100:02d}"
    return label[:match.start()] + new + label[match.end():]


def time_order(values) -> list[int]:
    """Row positions in TIME order for a period or date column (FY25, FY24 ... -> oldest first).
    Unreadable or missing labels keep their row order at the end. The one ordering used by pattern
    detection, generation, validation and repair, so they always read a series the same way."""
    import pandas as pd
    from src.core.dates import to_calendar_dates
    values = list(values)
    keys = []
    periods = [parse_period(v) if isinstance(v, str) else None for v in values]
    if any(p is not None for p in periods):
        keys = [p.ordinal if p is not None else None for p in periods]
    else:
        stamps = to_calendar_dates(pd.Series(values, dtype=object))[0]
        keys = [None if pd.isna(t) else t.value for t in stamps]
    known = sorted((k, i) for i, k in enumerate(keys) if k is not None)
    return [i for _, i in known] + [i for i, k in enumerate(keys) if k is None]


# ---------------------------------------------------------------- shifting periods inside free text
_FY_TOKEN = re.compile(r"\b(Q[1-4]|H[12])?(FY[ -]?)(\d{2}|\d{4})([AE])?\b", re.I)
_YEAR_TOKEN = re.compile(r"(?<!FY)(?<!FY )(?<!FY-)(?<!fy)(?<!fy )(?<!fy-)(?<![\d,.])((?:19|20)\d{2})(?![\d,])")   # FY years: _FY_TOKEN
_MONTH_YY = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)([a-z]*[-' ])(\d{2})\b", re.I)


def shift_text_periods(text: str, years: int) -> str:
    """Move every year and period label in a line of text forward by `years` (E-16), keeping its
    spelling: '11th September 2023 Q1FY24 RESULTS UPDATE' -> '11th September 2024 Q1FY25 RESULTS UPDATE'."""
    if not text or not years:
        return text

    def fy(m):
        digits = m.group(3)
        shifted = (int(digits) + years) % (100 if len(digits) == 2 else 10000)
        return f"{m.group(1) or ''}{m.group(2)}{shifted:0{len(digits)}d}{m.group(4) or ''}"

    out = _FY_TOKEN.sub(fy, text)
    out = _YEAR_TOKEN.sub(lambda m: str(int(m.group(1)) + years), out)
    return _MONTH_YY.sub(lambda m: f"{m.group(1)}{m.group(2)}{(int(m.group(3)) + years) % 100:02d}", out)
