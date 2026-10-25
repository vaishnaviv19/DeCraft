# src/inference/_formula.py
"""Safe formula engine (decision D-05). The LLM writes formulas as TEXT, e.g.
    "[Revenue] = [Units] * [Unit Price]"
This module parses that text with Python's `ast` module in 'eval' MODE (parsing only - nothing is
executed), walks the tree with an allowlist, and builds a contracts.Expr tree. evaluate() interprets
the tree itself over pandas Series. eval()/exec()/compile() of model text is forbidden in this codebase.

Formula language (everything else is rejected with FormulaSyntaxError):
  * columns written as [Column Name]; finite numeric constants with |value| <= MAX_ABS_CONST;
  * parentheses, unary + and -, binary + - * / // %   (no ** : exponentiation is not needed);
  * the functions in FUNCS. round() takes a constant digit count 0..MAX_ROUND_DIGITS.
    years_between()/days_between() take two plain [Column] arguments that hold dates, and such a date
    column may not also be used as a number;
  * limits: MAX_TEXT characters, MAX_NODES AST nodes, MAX_DEPTH nesting levels.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.contracts import Expr, FormulaSpec
from src.errors import FormulaSyntaxError
from src.inference._refdate import DEFAULT_TZ, completed_years, to_calendar_dates

COL_RE = re.compile(r"\[([^\[\]]+)\]")
PLACEHOLDER_RE = re.compile(r"__c\d+")          # internal names; never allowed in the model's text
FUNCS = {"sum": (1, None), "min": (1, None), "max": (1, None), "abs": (1, 1), "round": (2, 2),
         "years_between": (2, 2), "days_between": (2, 2), "pct_change": (2, 2), "ratio_pct": (2, 2)}
DATE_FUNCS = {"years_between", "days_between"}
BINOPS = {ast.Add: "add", ast.Sub: "sub", ast.Mult: "mul", ast.Div: "div",
          ast.FloorDiv: "floordiv", ast.Mod: "mod"}
MAX_TEXT, MAX_NODES, MAX_DEPTH = 300, 60, 12
MAX_ABS_CONST = 1e12
MAX_ROUND_DIGITS = 10


def parse_formula(text: str, columns: list[str]) -> FormulaSpec:
    """'[Out] = <expression>' -> FormulaSpec. Raises FormulaSyntaxError outside the allowlist."""
    if not isinstance(text, str):
        raise FormulaSyntaxError("formula must be text")
    if len(text) > MAX_TEXT or text.count("=") != 1:
        raise FormulaSyntaxError("formula must be one '<[output]> = <expression>' under 300 chars")
    left, right = (s.strip() for s in text.split("="))
    m = re.fullmatch(r"\[([^\[\]]+)\]", left)
    if not m:
        raise FormulaSyntaxError("left side must be a single [column]")
    output = m.group(1).strip()
    if PLACEHOLDER_RE.search(COL_RE.sub("", right)):
        raise FormulaSyntaxError("bare name: columns must be written as [Column Name]")
    names: dict[str, str] = {}

    def to_ident(mm: re.Match) -> str:
        col = mm.group(1).strip()
        if col not in columns:
            raise FormulaSyntaxError(f"unknown column: {col!r}")
        return names.setdefault(col, f"__c{len(names)}")

    if output not in columns:
        raise FormulaSyntaxError(f"unknown output column: {output!r}")
    py = COL_RE.sub(to_ident, right)
    try:
        tree = ast.parse(py, mode="eval")                 # parse only; never compiled or executed
    except SyntaxError as e:
        raise FormulaSyntaxError(f"syntax: {e.msg}") from e
    except (RecursionError, MemoryError) as e:
        raise FormulaSyntaxError("formula nested too deeply") from e
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise FormulaSyntaxError("formula too large")
    back = {v: k for k, v in names.items()}
    expr = _convert(tree.body, back, 0)
    spec = FormulaSpec(output=output, expr=expr, text=f"[{output}] = {right}")
    if output in spec.inputs:
        raise FormulaSyntaxError("output column cannot appear on the right side")
    dates, numbers = split_inputs(expr)
    if dates & numbers:
        raise FormulaSyntaxError("a date column cannot also be used as a number")
    return spec


def _convert(node: ast.AST, back: dict[str, str], depth: int) -> Expr:
    if depth > MAX_DEPTH:
        raise FormulaSyntaxError("formula nested too deeply")
    d = depth + 1
    if isinstance(node, ast.Name):
        if node.id not in back:
            raise FormulaSyntaxError(f"bare name {node.id!r}: columns must be written as [Column Name]")
        return Expr(op="col", col=back[node.id])
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        value = float(node.value)
        if not np.isfinite(value) or abs(value) > MAX_ABS_CONST:
            raise FormulaSyntaxError("numeric constant out of range")
        return Expr(op="const", value=value)
    if isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
        return Expr(op=BINOPS[type(node.op)], args=[_convert(node.left, back, d), _convert(node.right, back, d)])
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _convert(node.operand, back, d)
        return Expr(op="neg", args=[inner]) if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        name = node.func.id
        if name not in FUNCS:
            raise FormulaSyntaxError(f"function not allowed: {name}")
        lo, hi = FUNCS[name]
        if len(node.args) < lo or (hi is not None and len(node.args) > hi):
            raise FormulaSyntaxError(f"wrong number of arguments for {name}")
        args = [_convert(a, back, d) for a in node.args]
        if name == "round":
            digits = args[1].value
            if args[1].op != "const" or digits != int(digits) or not 0 <= digits <= MAX_ROUND_DIGITS:
                raise FormulaSyntaxError(f"round() needs a constant digit count 0..{MAX_ROUND_DIGITS}")
        if name in DATE_FUNCS and any(a.op != "col" for a in args):
            raise FormulaSyntaxError(f"{name}() takes two [Column] arguments")
        return Expr(op=name, args=args)
    raise FormulaSyntaxError(f"construct not allowed: {type(node).__name__}")


def split_inputs(e: Expr) -> tuple[set[str], set[str]]:
    """-> (columns used as dates, columns used as numbers)."""
    dates: set[str] = set()
    numbers: set[str] = set()
    stack = [e]
    while stack:
        x = stack.pop()
        if x.op == "col":
            numbers.add(x.col)
        elif x.op in DATE_FUNCS:
            dates.update(a.col for a in x.args)
        else:
            stack.extend(x.args)
    return dates, numbers


def evaluate(e: Expr, df: pd.DataFrame) -> pd.Series:
    """Row-wise interpreter over trusted pandas operations only. Division (/, //, %) by zero -> NaN
    (reported as invalid arithmetic, never a match)."""
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
        later, earlier = to_calendar_dates(a[0])[0], to_calendar_dates(a[1])[0]
        if e.op == "years_between":
            return completed_years(earlier, later)        # calendar arithmetic, not days / 365.25
        return (later - earlier).dt.days.astype(float)
    if e.op == "pct_change":
        new, old = a[0].astype(float), a[1].astype(float)
        return 100.0 * (new / old.where(old != 0) - 1)
    if e.op == "ratio_pct":
        num, den = a[0].astype(float), a[1].astype(float)
        return 100.0 * num / den.where(den != 0)
    raise FormulaSyntaxError(f"unsupported op {e.op}")


def rounding_tolerance(spec: FormulaSpec, df: pd.DataFrame, decimals: dict[str, int]) -> pd.Series:
    """Per-row absolute tolerance from DISPLAYED decimals: half a unit of the output's last digit plus
    the propagated rounding error of every numeric input (one-at-a-time perturbation)."""
    tol = pd.Series(0.5 * 10.0 ** -decimals.get(spec.output, 6), index=df.index)
    base = evaluate(spec.expr, df)
    for c in spec.inputs:
        if c in decimals and pd.api.types.is_numeric_dtype(df[c]):
            bumped = df.copy()
            bumped[c] = df[c] + 0.5 * 10.0 ** -decimals[c]
            tol = tol + (evaluate(spec.expr, bumped) - base).abs().fillna(0)
    return tol.where(np.isfinite(tol), 0.0)              # a non-finite tolerance would match anything


# ---------------------------------------------------------------- row-level helpers (shared by verifiers)
def positional_frame(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """The needed columns with a fresh 0..n-1 index (safe with duplicate index labels); blank strings
    become missing."""
    out = df[cols].reset_index(drop=True)
    for c in out.columns:
        s = out[c]
        if pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s):
            out[c] = s.mask(s.map(lambda v: isinstance(v, str) and not v.strip()).astype(bool))
    return out


def _float_or_nan(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError, OverflowError):
        return np.nan


def to_float(s: pd.Series) -> pd.Series:
    """Numbers -> float. Anything that is not a finite number (text, dates, inf) -> NaN."""
    if not (pd.api.types.is_numeric_dtype(s) or pd.api.types.is_object_dtype(s)
            or pd.api.types.is_string_dtype(s)):
        return pd.Series(np.nan, index=s.index)
    try:
        out = pd.to_numeric(s, errors="coerce").astype(float)
    except (TypeError, ValueError, OverflowError):
        out = s.map(_float_or_nan).astype(float)
    return out.where(np.isfinite(out))


@dataclass(frozen=True)
class FormulaCheck:
    """Row accounting for one formula test. Every row of the table is in exactly one bucket:
    missing (a required value is blank: skipped), unreadable (a value is present but is not a number /
    date: skipped), or TESTED. Tested rows are matched, mismatched, or undefined (the formula itself
    gives NaN/inf there, e.g. division by zero). Undefined rows count against the formula."""
    match_rate: float | None        # matched / n_rows; None when n_rows < min_rows
    n_rows: int                     # tested rows (the denominator)
    n_matched: int
    n_undefined: int
    n_unreadable: int
    n_missing: int

    @property
    def n_invalid(self) -> int:     # rows that could not be evaluated
        return self.n_undefined + self.n_unreadable


def check_formula(df: pd.DataFrame, spec: FormulaSpec, decimals: dict[str, int] | None = None,
                  rel_tol: float = 0.005, min_rows: int = 5, tz: str = DEFAULT_TZ) -> FormulaCheck:
    """Evaluate `spec` on every row and compare with the output column. Never raises for bad data."""
    frame = positional_frame(df, [spec.output, *spec.inputs])
    present = frame.notna().all(axis=1)
    n_missing = int((~present).sum())
    frame = frame[present].reset_index(drop=True)
    dates, _ = split_inputs(spec.expr)
    readable = pd.Series(True, index=frame.index)
    for c in list(frame.columns):
        if c in dates:
            frame[c], bad = to_calendar_dates(frame[c], tz)
        else:
            frame[c] = to_float(frame[c])
            bad = frame[c].isna()
        readable &= ~bad
    n_unreadable = int((~readable).sum())
    frame = frame[readable].reset_index(drop=True)
    n = len(frame)
    if n < min_rows:
        return FormulaCheck(None, n, 0, 0, n_unreadable, n_missing)
    with np.errstate(all="ignore"):                      # overflow / 0-division become inf/NaN, checked below
        calc = evaluate(spec.expr, frame).astype(float)
        actual = frame[spec.output]
        defined = pd.Series(np.isfinite(calc.to_numpy()), index=frame.index)
        tol = np.maximum(rel_tol * actual.abs(), rounding_tolerance(spec, frame, decimals or {}))
        ok = defined & ((calc - actual).abs() <= tol)
    return FormulaCheck(float(ok.mean()), n, int(ok.sum()), int((~defined).sum()), n_unreadable, n_missing)


def verify_formula(df: pd.DataFrame, spec: FormulaSpec, decimals: dict[str, int] | None = None,
                   rel_tol: float = 0.005, min_rows: int = 5):
    """-> (Match Rate | None, rows tested, invalid rows). None when fewer than min_rows are testable.
    See FormulaCheck for the counting rules."""
    r = check_formula(df, spec, decimals, rel_tol, min_rows)
    return r.match_rate, r.n_rows, r.n_invalid
