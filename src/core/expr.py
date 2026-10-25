# src/core/expr.py
"""Interpreter for the Formula Expr tree (Shared Kernel).

The parser lives in the inference module's `_formula.py` (model text is parsed there, once, under the
allowlist). Generation only ever evaluates the tree that verification already accepted, so it lives
here and no module needs another module's internals. This is the ONLY evaluator: inference
re-exports it, so the two can never drift apart.
"""
from __future__ import annotations

import pandas as pd

from src.core.dates import completed_years, to_calendar_dates


def evaluate(e, df: pd.DataFrame) -> pd.Series:
    """Row-wise interpreter over trusted pandas operations only. Division by zero -> NaN."""
    idx = df.index
    if e.op == "col":
        return df[e.col]
    if e.op == "const":
        return pd.Series(e.value, index=idx, dtype=float)
    a = [evaluate(x, df) for x in e.args]
    if e.op in ("add", "sub", "mul", "div", "floordiv", "mod"):
        x, y = a[0].astype(float), a[1].astype(float)
        if e.op in ("div", "floordiv", "mod"):
            y = y.where(y != 0)
            return {"div": x / y, "floordiv": x // y, "mod": x % y}[e.op]
        return {"add": x + y, "sub": x - y, "mul": x * y}[e.op]
    if e.op == "neg":
        return -a[0].astype(float)
    if e.op in ("sum", "min", "max"):
        frame = pd.concat([s.astype(float) for s in a], axis=1)
        return getattr(frame, e.op)(axis=1, skipna=False)
    if e.op == "abs":
        return a[0].astype(float).abs()
    if e.op == "round":
        return a[0].astype(float).round(int(e.args[1].value))
    if e.op in ("years_between", "days_between"):
        # calendar dates under the shared policy: day/month never guessed, calendar-year arithmetic
        later, earlier = to_calendar_dates(a[0])[0], to_calendar_dates(a[1])[0]
        return completed_years(earlier, later) if e.op == "years_between" \
            else (later - earlier).dt.days.astype(float)
    if e.op == "pct_change":
        new, old = a[0].astype(float), a[1].astype(float)
        return 100.0 * (new / old.where(old != 0) - 1)
    if e.op == "ratio_pct":
        num, den = a[0].astype(float), a[1].astype(float)
        return 100.0 * num / den.where(den != 0)
    raise ValueError(f"unsupported op {e.op}")
