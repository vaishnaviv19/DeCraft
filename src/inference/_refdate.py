# src/inference/_refdate.py
"""Reference date for date-based derivations such as Age from DOB (decision D-19).
Priority: explicit 'as of / as at / year ended <date>' in the text -> latest period end on any time
axis -> None (the rule then becomes untestable and its tier is 'semantic')."""
from __future__ import annotations

import re
from datetime import date

import pandas as pd

from src.core.dates import (DEFAULT_TZ, LEAP_POLICIES, calendar_date, completed_years,  # noqa: F401
                            to_calendar_dates)
from src.core.periods import Period

DATE_RE = re.compile(
    r"(?:as of|as at|year ended|period ended|quarter ended)\s+"
    r"(?:(?P<day>\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})"
    r"|(?P<month_year>[A-Za-z]+\s+\d{4}))", re.I)


def period_end(p: Period, fy_end_month: int | None = None) -> date:
    if p.kind == "year":
        month = fy_end_month if p.fiscal and fy_end_month else 12
        return (pd.Timestamp(year=p.year, month=month, day=1) + pd.offsets.MonthEnd(0)).date()
    if p.kind == "quarter":
        return (pd.Timestamp(year=p.year, month=3 * p.sub, day=1) + pd.offsets.MonthEnd(0)).date()
    return (pd.Timestamp(year=p.year, month=p.sub, day=1) + pd.offsets.MonthEnd(0)).date()


def infer_reference_date(text: str, periods: list[Period], fy_end_month: int | None = None) -> date | None:
    if m := DATE_RE.search(text or ""):
        d = pd.to_datetime(m.group("day") or m.group("month_year"), errors="coerce")
        if not pd.isna(d):
            return (pd.Timestamp(d) + pd.offsets.MonthEnd(0)).date() if m.group("month_year") else d.date()
    if periods:
        return max(period_end(p, fy_end_month) for p in periods)
    return None
