# src/core/dates.py
"""Calendar-date policy shared by inference, privacy and generation (Shared Kernel)."""
from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd

# ---------------------------------------------------------------- calendar-date policy
# Rules such as "Age from DOB" are defined on CALENDAR DATES, so every date value is reduced to a date:
#   * tz-aware values are converted to DEFAULT_TZ first, then the calendar date is taken;
#   * naive values are already local calendar dates; any time of day is dropped;
#   * numeric day/month strings such as 03/04/2025 are ambiguous. They are read with a trusted
#     `date_format` when the caller has one, otherwise with the order the column's own unambiguous
#     values prove (a first part > 12 -> day first; a second part > 12 -> month first). With no or
#     conflicting evidence those values are INVALID - never guessed;
#   * only full dates are accepted (a year, a month and a day). '2025', 'March 2024' or plain numbers
#     (spreadsheet serials) are invalid, so nothing is filled in from today's date. A 2-digit year is
#     accepted only with the month written as a word ('14-May-21'), read with Python's %y rule
#     (00-68 -> 20xx, 69-99 -> 19xx); day and month cannot be confused there.
DEFAULT_TZ = "UTC"
LEAP_POLICIES = ("mar1", "feb28")
_ISO_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(?:Z|[+-]\d{2}:?\d{2})?)?$")
_YMD_SLASH_RE = re.compile(r"^(\d{4})[/.](\d{1,2})[/.](\d{1,2})$")
_NUMERIC_RE = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$")
_TEXT_YY_RE = re.compile(r"^\d{1,2}[-\s/][A-Za-z]{3,9}\.?[-\s/,]+(?:\d{2}|\d{4})$")
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
        elif _TEXT_YY_RE.match(s):
            parts = re.split(r"[-\s/,.]+", s)
            parts[1] = parts[1][:3] if parts[1].lower().startswith("sept") else parts[1]
            for fmt in ("%d %b %y", "%d %B %y", "%d %b %Y", "%d %B %Y"):
                try:
                    ts = pd.Timestamp(datetime.strptime(" ".join(parts[:3]), fmt))
                    break
                except ValueError:
                    continue
            else:
                return None
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
