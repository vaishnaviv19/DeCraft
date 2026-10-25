# src/inference/_verifiers.py
"""Verifiers test one Proposal on the actual rows. classify_rule() is THE single policy function that
turns a test result into tier, status and enforcement (decisions D-03, D-04, D-16, D-17).

The LLM only PROPOSES. Nothing in a proposal is executed: formulas go through the allowlist parser in
_formula.py, date rules are predefined typed operations whose columns and reference date are checked
here, and every number is computed by this code.

Row accounting (same for every verifier that reads typed values):
  missing     a required value is blank                         -> skipped (n_missing)
  unreadable  a value is present but not a number / full date   -> skipped, counted in n_invalid
  undefined   values are fine but the rule has no answer there  -> TESTED, never a match, in n_invalid
              (division by zero, overflow/inf, reference date before birth)
  tested      n_rows = the match-rate denominator; match_rate = matched / n_rows
Fewer than min_rows tested rows -> match_rate None (untestable).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.contracts import DecisionStatus, Enforcement, ExtractionGrade, RuleTier
from src.errors import FormulaSyntaxError
from src.inference._formula import check_formula, parse_formula, positional_frame, to_float
from src.inference._refdate import DEFAULT_TZ, calendar_date, completed_years, to_calendar_dates

AGE_TOL_YEARS = 0       # completed age is exact; a source that rounds ages would need a wider tolerance


@dataclass
class VerifyResult:
    match_rate: float | None      # None = could not be tested
    n_rows: int                   # rows tested (the match-rate denominator)
    detail: str
    n_invalid: int = 0            # rows that could not be evaluated: unreadable or undefined (see above)
    rejected: bool = False        # the proposal itself was refused (unsafe, malformed or unsupported)
    n_missing: int = 0            # rows skipped because a required value is blank


def _rejected(reason: str) -> VerifyResult:
    return VerifyResult(0.0, 0, f"rejected {reason}", rejected=True)


def _summary(label: str, n: int, matched: int, undefined: int, unreadable: int, missing: int) -> str:
    """Counts only - never row values (they may be sensitive)."""
    return (f"{label}: {matched}/{n} tested rows match ({n - matched - undefined} mismatched, "
            f"{undefined} undefined); skipped {unreadable} unreadable and {missing} missing")


def _present(df: pd.DataFrame, cols: list[str]) -> tuple[pd.DataFrame, int]:
    frame = positional_frame(df, cols)
    keep = frame.notna().all(axis=1)
    return frame[keep].reset_index(drop=True), int((~keep).sum())


# ---------------------------------------------------------------- date relationships
def verify_age_from_dob(df, dob, age, ref_date=None, tol_years=AGE_TOL_YEARS, min_rows=5, *,
                        ref_col: str | None = None, tz: str = DEFAULT_TZ,
                        date_formats: dict[str, str] | None = None, leap_policy: str = "mar1") -> VerifyResult:
    """[age] is the completed age in years at a reference date (calendar arithmetic, see
    _refdate.completed_years; Feb 29 birthdays follow `leap_policy`).
    Give exactly one reference: `ref_date` = one trusted date for every row (document 'as at' date or an
    injected run date), or `ref_col` = a column holding each row's own reference date. A row's own date
    is never replaced by a shared one. `date_formats` ({column: strptime format}) and `tz` come from
    trusted code only."""
    if (ref_date is None) == (ref_col is None):
        raise ValueError("give exactly one of ref_date or ref_col")
    fmts = date_formats or {}
    frame, n_missing = _present(df, [dob, age] + ([ref_col] if ref_col else []))
    birth, bad = to_calendar_dates(frame[dob], tz, fmts.get(dob))
    if ref_col:
        ref, bad_ref = to_calendar_dates(frame[ref_col], tz, fmts.get(ref_col))
        bad = bad | bad_ref
        where = f"per-row reference date [{ref_col}]"
    else:
        shared = calendar_date(ref_date, tz)
        ref = pd.Series(shared, index=frame.index, dtype="datetime64[ns]")
        where = f"reference date {shared.date()}"
    target = to_float(frame[age])
    readable = ~(bad | target.isna())
    n_unreadable = int((~readable).sum())
    birth, ref, target = birth[readable], ref[readable], target[readable]
    n = len(target)
    if n < min_rows:
        return VerifyResult(None, n, f"too few rows: age from dob ({where}); {n} tested, "
                            f"skipped {n_unreadable} unreadable and {n_missing} missing",
                            n_unreadable, n_missing=n_missing)
    undefined = ref < birth                              # not born yet at the reference date
    ok = ~undefined & ((completed_years(birth, ref, leap_policy) - target).abs() <= tol_years)
    n_undefined = int(undefined.sum())
    return VerifyResult(float(ok.mean()), n,
                        _summary(f"age from dob ({where})", n, int(ok.sum()), n_undefined, n_unreadable, n_missing),
                        n_unreadable + n_undefined, n_missing=n_missing)


def verify_ordering(df, earlier, later, min_gap_days=0, min_rows=5, *, tz: str = DEFAULT_TZ,
                    date_formats: dict[str, str] | None = None) -> VerifyResult:
    fmts = date_formats or {}
    frame, n_missing = _present(df, [earlier, later])
    e, bad_e = to_calendar_dates(frame[earlier], tz, fmts.get(earlier))
    lt, bad_l = to_calendar_dates(frame[later], tz, fmts.get(later))
    readable = ~(bad_e | bad_l)
    n_unreadable = int((~readable).sum())
    gap = (lt[readable] - e[readable]).dt.days
    if len(gap) < min_rows:
        return VerifyResult(None, len(gap), "too few rows", n_unreadable, n_missing=n_missing)
    ok = gap >= min_gap_days
    return VerifyResult(float(ok.mean()), len(gap),
                        _summary("ordering", len(gap), int(ok.sum()), 0, n_unreadable, n_missing),
                        n_unreadable, n_missing=n_missing)


# ---------------------------------------------------------------- other relationships
def verify_determines(df, key, val, min_rows=5) -> VerifyResult:
    sub = df[[key, val]].dropna()
    counts = sub.groupby(key)[val].agg(["nunique", "size"])
    repeated = counts[counts["size"] >= 2]
    if repeated.empty:
        return VerifyResult(None, len(sub), "no repeated keys: untestable")
    return VerifyResult(float((repeated["nunique"] == 1).mean()), int(len(repeated)), "functional dependency")


def verify_conditional(df, cond_col, eq, target_col, min_rows=5) -> VerifyResult:
    hit = df[df[cond_col] == eq]
    if len(hit) < min_rows:
        return VerifyResult(None, len(hit), "condition rarely true")
    return VerifyResult(float(hit[target_col].notna().mean()), len(hit), "conditional notnull")


# ---------------------------------------------------------------- formulas
def verify_formula_text(df, text, decimals, th) -> VerifyResult:
    try:
        spec = parse_formula(text, list(df.columns))
    except FormulaSyntaxError as e:
        return _rejected(f"formula: {e}")
    r = check_formula(df, spec, decimals, th.rel_tol_default, th.min_rows_rule)
    return VerifyResult(r.match_rate, r.n_rows,
                        _summary(spec.text, r.n_rows, r.n_matched, r.n_undefined, r.n_unreadable, r.n_missing),
                        r.n_invalid, n_missing=r.n_missing)


def proposal_formula_text(p) -> str:
    """The '[Out] = <expression>' text of a formula proposal. params: 'expression' (required) and
    optional 'output_column' for an expression written without '[Out] ='. Raises FormulaSyntaxError."""
    params = p.params if isinstance(p.params, dict) else {}
    expr, out = params.get("expression", ""), params.get("output_column")
    if not isinstance(expr, str) or (out is not None and not isinstance(out, str)):
        raise FormulaSyntaxError("expression and output_column must be text")
    if out is None:
        return expr
    if "=" not in expr:
        return f"[{out.strip()}] = {expr}"
    if expr.split("=", 1)[0].strip() != f"[{out.strip()}]":
        raise FormulaSyntaxError("output_column does not match the left side of the expression")
    return expr


# ---------------------------------------------------------------- tier and the single policy
def tier_from_result(res: VerifyResult, th) -> RuleTier:
    """Bands: >= strong strongly_inferred | >= hypothesis hypothesized | >= contradicted unknown | else
    contradicted. Rejected proposals -> contradicted. Untestable (None or fewer than min_rows_rule rows)
    -> semantic (D-03), EXCEPT when rows had to be thrown away as invalid: then the rule is not known to
    be plausible, so it is 'unknown' and can never become a HARD rule by accident."""
    if res.rejected or res.detail.startswith("rejected formula"):
        return RuleTier.CONTRADICTED
    if res.match_rate is None or res.n_rows < th.min_rows_rule:
        return RuleTier.UNKNOWN if res.n_invalid > 0 else RuleTier.SEMANTIC
    if res.match_rate >= th.formula_strong_match:
        return RuleTier.STRONGLY_INFERRED
    if res.match_rate >= th.formula_hypothesis_match:
        return RuleTier.HYPOTHESIZED
    if res.match_rate >= th.contradicted_match:
        return RuleTier.UNKNOWN
    return RuleTier.CONTRADICTED


ENFORCED = {RuleTier.EXPLICIT, RuleTier.STRONGLY_INFERRED, RuleTier.SEMANTIC}


def classify_rule(res: VerifyResult, th, stated_in_text: bool = False,
                  grade: ExtractionGrade = ExtractionGrade.GOOD
                  ) -> tuple[RuleTier, DecisionStatus, Enforcement, str]:
    """The ONLY place that decides whether a rule is enforced. Confidence scores never decide it."""
    tier = tier_from_result(res, th)
    if res.rejected:
        reasons = [f"proposal rejected before testing ({res.detail}) -> {tier.value}"]
    else:
        reasons = [f"match_rate={res.match_rate} on {res.n_rows} rows ({res.n_invalid} invalid) -> {tier.value}"]
    if stated_in_text and tier in ENFORCED | {RuleTier.HYPOTHESIZED}:
        tier = RuleTier.EXPLICIT
        reasons.append("stated in the PDF text and not contradicted by the data")
    elif stated_in_text:
        reasons.append("stated in the PDF text but not supported by the data -> needs review")
    if grade == ExtractionGrade.DOUBTFUL and tier in ENFORCED:
        tier = RuleTier.HYPOTHESIZED
        reasons.append("extraction quality doubtful -> capped at hypothesized")
    if tier in ENFORCED:
        return tier, DecisionStatus.ACCEPTED, Enforcement.HARD, "; ".join(reasons)
    if tier == RuleTier.CONTRADICTED:
        return tier, DecisionStatus.REJECTED, Enforcement.NONE, "; ".join(reasons)
    return tier, DecisionStatus.NEEDS_REVIEW, Enforcement.NONE, "; ".join(reasons)


# ---------------------------------------------------------------- dispatch
REFERENCE_HELP = ("reference_date must be {'column': <name>}, {'source': 'document'} or "
                  "{'source': 'run_date'}; a literal date in a proposal is not trusted")


def _reference(spec) -> tuple[str, str | None]:
    """Proposal 'reference_date' -> ('document' | 'run_date' | 'column', column name or None)."""
    if spec is None:
        return "document", None                          # D-19 default: the document's own date
    if isinstance(spec, dict) and len(spec) == 1:
        ((k, v),) = spec.items()
        if k == "column" and isinstance(v, str):
            return "column", v
        if k == "source" and v in ("document", "run_date"):
            return v, None
    raise ValueError(REFERENCE_HELP)


def _verify_date_derivation(df, p, ref_date, th, run_date, tz, date_formats) -> VerifyResult:
    """derived_from: columns=[derived, parent] params={'unit': 'years'} plus optional
    'operation': 'age_from_dates', 'output_column', 'birth_date_column', 'reference_date'."""
    params, c = p.params, list(p.columns)
    if params.get("unit") != "years":
        return _rejected("proposal: derived_from supports only unit 'years'")
    if params.get("operation", "age_from_dates") != "age_from_dates":
        return _rejected("proposal: unsupported date operation")
    target = params.get("output_column", c[0] if c else None)
    birth = params.get("birth_date_column", c[1] if len(c) > 1 else None)
    if (len(c) > 0 and target != c[0]) or (len(c) > 1 and birth != c[1]):
        return _rejected("proposal: columns and params name different columns")
    try:
        source, ref_col = _reference(params.get("reference_date"))
    except ValueError as e:
        return _rejected(f"proposal: {e}")
    used = [target, birth] + ([ref_col] if ref_col is not None else [])
    if not all(isinstance(x, str) and x in df.columns for x in used) or len(set(used)) != len(used):
        return _rejected("proposal: needs distinct known columns for age, birth date and reference date")
    kw = dict(min_rows=th.min_rows_rule, tz=tz, date_formats=date_formats)
    if source == "column":
        return verify_age_from_dob(df, birth, target, ref_col=ref_col, **kw)
    trusted = ref_date if source == "document" else run_date
    if trusted is None:
        return VerifyResult(None, 0, "no reference date: untestable" if source == "document"
                            else "no run date supplied: untestable")
    return verify_age_from_dob(df, birth, target, trusted, **kw)


def _is_scalar(v) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def verify_proposal(df, p, ref_date, decimals, th, *, run_date=None, tz: str = DEFAULT_TZ,
                    date_formats: dict[str, str] | None = None) -> VerifyResult:
    """`p` is an Annotator Proposal (kind, columns, params). Duck-typed to keep this module independent.
    Trusted inputs only come from the caller: `ref_date` is the document reference date (D-19),
    `run_date` the injected execution date for rules explicitly 'as of the run', `tz` the timezone policy
    and `date_formats` known column date formats. A malformed or unsupported proposal is rejected; it
    never raises and never reaches an untestable (enforceable) state by accident."""
    if not isinstance(p.params, dict):
        return _rejected("proposal: params must be an object")
    if p.kind == "formula":
        try:
            text = proposal_formula_text(p)
        except FormulaSyntaxError as e:
            return _rejected(f"formula: {e}")
        return verify_formula_text(df, text, decimals, th)
    if p.kind == "derived_from":
        return _verify_date_derivation(df, p, ref_date, th, run_date, tz, date_formats)
    c = list(p.columns)
    if p.kind not in ("determines", "hierarchy", "ordering", "conditional"):
        return _rejected("proposal: unsupported kind")
    if len(c) < 2 or c[0] == c[1] or not all(isinstance(x, str) and x in df.columns for x in c[:2]):
        return _rejected("proposal: needs two distinct known columns")
    if p.kind in ("determines", "hierarchy"):
        key, val = (c[0], c[1]) if p.kind == "determines" else (c[1], c[0])   # child determines parent
        return verify_determines(df, key, val, th.min_rows_rule)
    if p.kind == "ordering":
        gap = p.params.get("min_gap_days", 0)
        if isinstance(gap, bool) or not isinstance(gap, (int, float)) or not abs(gap) < 1e6:
            return _rejected("proposal: min_gap_days must be a number of days")
        return verify_ordering(df, c[0], c[1], gap, th.min_rows_rule, tz=tz, date_formats=date_formats)
    eq = p.params.get("eq")
    if not _is_scalar(eq):
        return _rejected("proposal: eq must be a single value")
    return verify_conditional(df, c[0], eq, c[1], th.min_rows_rule)
