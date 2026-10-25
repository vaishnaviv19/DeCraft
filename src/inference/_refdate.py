# src/inference/_refdate.py
"""Reference date for date-based derivations such as Age from DOB (decision D-19).
Priority: explicit 'as of / as at / year ended <date>' in the text -> latest period end on any time
axis -> None (the rule then becomes untestable and its tier is 'semantic')."""
from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd

from src.core.periods import Period

DATE_RE = re.compile(
    r"(?:as of|as at|year ended|period ended|quarter ended)\s+"
    r"(?:(?P<day>\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2})"
    r"|(?P<month_year>[A-Za-z]+\s+\d{4}))", re.I)


# ---------------------------------------------------------------- calendar-date policy
# Rules such as "Age from DOB" are defined on CALENDAR DATES, so every date value is reduced to a date:
#   * tz-aware values are converted to DEFAULT_TZ first, then the calendar date is taken;
#   * naive values are already local calendar dates; any time of day is dropped;
#   * numeric day/month strings such as 03/04/2025 are ambiguous. They are read with a trusted
#     `date_format` when the caller has one, otherwise with the order the column's own unambiguous
#     values prove (a first part > 12 -> day first; a second part > 12 -> month first). With no or
#     conflicting evidence those values are INVALID - never guessed;
#   * only full dates are accepted (a 4-digit year, a month and a day). '2025', 'March 2024' or plain
#     numbers (spreadsheet serials) are invalid, so nothing is filled in from today's date.
DEFAULT_TZ = "UTC"
LEAP_POLICIES = ("mar1", "feb28")
_ISO_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(?:Z|[+-]\d{2}:?\d{2})?)?$")
_YMD_SLASH_RE = re.compile(r"^(\d{4})[/.](\d{1,2})[/.](\d{1,2})$")
_NUMERIC_RE = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$")
_TEXT_RE = re.compile(r"^(?:\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})$")


def _numeric_order(values: list[str]) -> str | None:
    """'dmy' or 'mdy' when the column's unambiguous numeric dates prove it, else None."""
    dmy = mdy = False
    for v in values:
        if m := _NUMERIC_RE.match(v):
            a, b = int(m.group(1)), int(m.group(2))
            dmy |= a > 12 >= b
            mdy |= b > 12 >= a
    return ("dmy" if dmy else "mdy") if dmy != mdy else None


def _parse_one(v, tz: str, date_format: str | None, order: str | None) -> pd.Timestamp | None:
    """One value -> calendar date (naive midnight Timestamp), or None when it cannot be read."""
    if isinstance(v, (pd.Timestamp, datetime, date)):
        ts = pd.Timestamp(v)
    elif isinstance(v, str):
        s = v.strip()
        if date_format:
            ts = pd.to_datetime(s, format=date_format)
        elif m := _NUMERIC_RE.match(s):
            if order is None:
                return None                                   # ambiguous day/month: do not guess
            a, b, y = (int(g) for g in m.groups())
            day, month = (a, b) if order == "dmy" else (b, a)
            ts = pd.Timestamp(year=y, month=month, day=day)
        elif m := _YMD_SLASH_RE.match(s):
            ts = pd.Timestamp(year=int(m.group(1)), month=int(m.group(2)), day=int(m.group(3)))
        elif _ISO_RE.match(s) or _TEXT_RE.match(s):
            ts = pd.Timestamp(s)
        else:
            return None
    else:
        return None                                           # numbers, lists ...: not a date
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        ts = ts.tz_convert(tz).tz_localize(None)
    return ts.normalize()


def to_calendar_dates(values: pd.Series, tz: str = DEFAULT_TZ,
                      date_format: str | None = None) -> tuple[pd.Series, pd.Series]:
    """-> (calendar dates as naive datetime64, invalid mask) on the same index as `values`.
    Missing values stay NaT and are NOT invalid; a present value that cannot be read is NaT AND invalid.
    Never raises for bad data. `date_format` must come from trusted code, never from a proposal."""
    if isinstance(values.dtype, pd.DatetimeTZDtype):
        d = values.dt.tz_convert(tz).dt.tz_localize(None).dt.normalize()
        return d, pd.Series(False, index=values.index)
    if pd.api.types.is_datetime64_dtype(values):
        return values.dt.normalize(), pd.Series(False, index=values.index)
    missing = values.isna() | values.map(lambda v: isinstance(v, str) and not v.strip()).astype(bool)
    order = None if date_format else _numeric_order(
        [v.strip() for v in values[~missing] if isinstance(v, str)])
    out, bad = [], []
    for v, miss in zip(values, missing):
        ts = None
        if not miss:
            try:
                ts = _parse_one(v, tz, date_format, order)
            except (ValueError, TypeError, OverflowError, pd.errors.OutOfBoundsDatetime):
                ts = None
        out.append(pd.NaT if ts is None else ts)
        bad.append(ts is None and not miss)
    dates = pd.Series(pd.to_datetime(out), index=values.index, dtype=None)
    if not pd.api.types.is_datetime64_dtype(dates):           # e.g. every value missing
        dates = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    return dates, pd.Series(bad, index=values.index, dtype=bool)


def calendar_date(value, tz: str = DEFAULT_TZ) -> pd.Timestamp:
    """A trusted scalar date (document reference date, run date) -> naive midnight Timestamp."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert(tz).tz_localize(None)
    return ts.normalize()


def completed_years(birth: pd.Series, ref: pd.Series, leap_policy: str = "mar1") -> pd.Series:
    """Completed years from `birth` to `ref` by calendar arithmetic (not days / 365.25):
    reference year - birth year, minus one when the birthday has not yet come in the reference year.
    Feb 29 birthdays in a non-leap reference year: 'mar1' (default) -> the birthday is reached on
    1 March; 'feb28' -> on 28 February. NaN where either date is missing. A negative value means
    `ref` is before `birth`; callers decide what that means."""
    if leap_policy not in LEAP_POLICIES:
        raise ValueError(f"leap_policy must be one of {LEAP_POLICIES}")
    b_md = birth.dt.month * 100 + birth.dt.day
    r_md = ref.dt.month * 100 + ref.dt.day
    if leap_policy == "feb28":
        b_md = b_md.where(~((b_md == 229) & ~ref.dt.is_leap_year.astype(bool)), 228)
    years = (ref.dt.year - birth.dt.year).astype(float) - (r_md < b_md).astype(float)
    backwards = ref < birth                                   # count completed years the other way
    if backwards.any():
        years = years.where(~backwards, -completed_years(ref.where(backwards), birth.where(backwards),
                                                          leap_policy))
    return years.where(birth.notna() & ref.notna())


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
