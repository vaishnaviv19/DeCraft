# src/core/periods.py
"""Typed time labels and the time axis (M1.4). Fiscal labels stay fiscal."""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

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


def detect_axis(labels: list[str]):
    """Return (periods, kind, is_regular) or None when under 80% of labels parse as one kind."""
    ps = [parse_period(lab) for lab in labels]
    ok = [p for p in ps if p]
    if not labels or len(ok) < 0.8 * len(labels) or len({p.kind for p in ok}) != 1:
        return None
    ords = [p.ordinal for p in ok]
    return ps, ok[0].kind, all(b - a == 1 for a, b in zip(ords, ords[1:]))


def fiscal_year_end_month(text: str) -> int | None:
    m = FYE_RE.search(text or "")
    return MONTHS.get(m.group(1)[:3].lower()) if m else None


def detect_cumulative(values, periods, header: str = "", min_years: int = 2) -> bool:
    if any(w in (header or "").lower() for w in CUM_WORDS):
        return True
    if not periods or periods[0].kind == "year":
        return False
    years = sorted({p.year for p in periods})
    if len(years) < min_years:
        return False
    rising = resets = 0
    for y in years:
        v = [values[i] for i, p in enumerate(periods) if p.year == y]
        rising += all(b >= a for a, b in zip(v, v[1:]))
    for a, b in zip(years, years[1:]):
        last = [values[i] for i, p in enumerate(periods) if p.year == a][-1]
        first = [values[i] for i, p in enumerate(periods) if p.year == b][0]
        resets += first < last
    return rising == len(years) and resets == len(years) - 1


def decumulate(values, periods) -> list[float]:
    out = []
    for i, (v, p) in enumerate(zip(values, periods)):
        new_year = i == 0 or periods[i - 1].year != p.year
        out.append(v if new_year else v - values[i - 1])
    return out


def cumulate(flows, periods) -> list[float]:
    out, run = [], 0.0
    for i, (f, p) in enumerate(zip(flows, periods)):
        run = f if (i == 0 or periods[i - 1].year != p.year) else run + f
        out.append(run)
    return out


def yoy_pct(levels, lag: int) -> list[float | None]:
    return [None] * lag + [100.0 * (levels[i] / levels[i - lag] - 1) if levels[i - lag] else None
                           for i in range(lag, len(levels))]


def verify_yoy(levels, pct, lag: int, tol_pp: float = 0.1, min_rows: int = 3):
    """Match Rate of the displayed YoY percentages; (None, n) when too few rows are testable."""
    calc = yoy_pct(levels, lag)
    pairs = [(c, p) for c, p in zip(calc, pct) if c is not None and p is not None]
    if len(pairs) < min_rows:
        return None, len(pairs)
    return float(np.mean([abs(c - p) <= tol_pp for c, p in pairs])), len(pairs)
