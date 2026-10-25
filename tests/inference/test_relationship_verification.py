# tests/inference/test_relationship_verification.py
"""Secure verification of LLM-proposed relationships: the trusted formula interpreter, date rules with
shared / per-row / run-date references, row-level invalid handling, and the single enforcement policy."""
import ast
import builtins
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.contracts import DecisionStatus, Enforcement, ExtractionGrade, RuleTier
from src.inference._annotator import Proposal
from src.inference._formula import MAX_DEPTH, check_formula, parse_formula
from src.inference._refdate import completed_years, to_calendar_dates
from src.inference._verifiers import (classify_rule, tier_from_result, verify_age_from_dob,
                                      verify_formula_text, verify_ordering, verify_proposal)

REF = date(2025, 6, 30)


def formula(expression, **params):
    return Proposal(kind="formula", columns=[], params={"expression": expression, **params})


def age_rule(**params):
    return Proposal(kind="derived_from", columns=["Age", "DOB"], params={"unit": "years", **params})


def policy(res, cfg, stated=False, grade=ExtractionGrade.GOOD):
    return classify_rule(res, cfg.thresholds, stated, grade)[:3]


@pytest.fixture
def orders_df():
    q = [2, 5, 1, 10, 3, 4]
    p = [10.0, 4.0, 99.99, 1.5, 20.0, 7.25]
    d = [0, 10, 5, 20, 50, 0]
    s = [5.0, 0.0, 4.99, 2.0, 0.0, 1.0]
    total = [qi * pi * (1 - di / 100) + si for qi, pi, di, si in zip(q, p, d, s)]
    return pd.DataFrame({"quantity": q, "unit_price": p, "discount_pct": d, "shipping": s,
                         "total_amount": total})


@pytest.fixture
def no_code_execution(monkeypatch):
    """Fail the test if anything evaluates or compiles code. ast.parse itself calls compile() with
    PyCF_ONLY_AST (a parse, not a code object); that one call is allowed."""
    real_compile = builtins.compile

    def guarded_compile(source, filename, mode, flags=0, *a, **kw):
        if not flags & ast.PyCF_ONLY_AST:
            raise AssertionError("compile() to code was called")
        return real_compile(source, filename, mode, flags, *a, **kw)

    def forbidden(*a, **kw):
        raise AssertionError("eval()/exec() was called")

    monkeypatch.setattr(builtins, "compile", guarded_compile)
    monkeypatch.setattr(builtins, "eval", forbidden)
    monkeypatch.setattr(builtins, "exec", forbidden)


# ================================================================ arithmetic formulas
def test_simple_formula_exact_matches(sales_df, cfg):
    r = verify_formula_text(sales_df, "[Revenue] = [Units] * [Unit Price]", {}, cfg.thresholds)
    assert (r.match_rate, r.n_rows, r.n_invalid, r.rejected) == (1.0, 6, 0, False)


def test_complex_formula_with_parentheses_and_output_column(orders_df, cfg, no_code_execution):
    p = formula("[quantity] * [unit_price] * (1 - [discount_pct] / 100) + [shipping]",
                output_column="total_amount")
    r = verify_proposal(orders_df, p, None, {}, cfg.thresholds)
    assert r.match_rate == 1.0 and r.n_rows == 6
    assert policy(r, cfg) == (RuleTier.STRONGLY_INFERRED, DecisionStatus.ACCEPTED, Enforcement.HARD)


def test_output_column_must_agree_with_the_expression(orders_df, cfg):
    p = formula("[shipping] = [quantity] * 2", output_column="total_amount")
    assert verify_proposal(orders_df, p, None, {}, cfg.thresholds).rejected


def test_floor_division_and_modulo(cfg):
    df = pd.DataFrame({"minutes": [125, 60, 59, 600, 1, 61]})
    df["hours"], df["rest"] = df["minutes"] // 60, df["minutes"] % 60
    assert verify_formula_text(df, "[hours] = [minutes] // 60", {}, cfg.thresholds).match_rate == 1.0
    assert verify_formula_text(df, "[rest] = [minutes] % 60", {}, cfg.thresholds).match_rate == 1.0


def test_partially_matching_formula_is_hypothesized(cfg):
    a = [float(i) for i in range(1, 11)]
    df = pd.DataFrame({"a": a, "b": [2 * x for x in a]})
    df.loc[[3, 7], "b"] = -1.0
    r = verify_formula_text(df, "[b] = 2 * [a]", {}, cfg.thresholds)
    assert r.match_rate == 0.8 and r.n_rows == 10
    assert policy(r, cfg) == (RuleTier.HYPOTHESIZED, DecisionStatus.NEEDS_REVIEW, Enforcement.NONE)


def test_missing_numeric_values_are_skipped_not_mismatched(cfg):
    df = pd.DataFrame({"a": [1.0, 2, None, 4, 5, 6, 7, 8], "b": [2.0, 4, 6, None, 10, 12, "", 16]})
    r = verify_formula_text(df, "[b] = 2 * [a]", {}, cfg.thresholds)
    assert (r.match_rate, r.n_rows, r.n_missing, r.n_invalid) == (1.0, 5, 3, 0)


def test_division_by_zero_in_some_rows_counts_against_the_formula(cfg):
    df = pd.DataFrame({"a": [2.0] * 10, "b": [1, 2, 1, 2, 1, 2, 1, 2, 0, 0],
                       "r": [2.0, 1, 2, 1, 2, 1, 2, 1, 5, 5]})
    r = verify_formula_text(df, "[r] = [a] / [b]", {}, cfg.thresholds)
    assert (r.match_rate, r.n_rows, r.n_invalid) == (0.8, 10, 2)


def test_non_finite_results_and_values_are_invalid(cfg):
    overflow = pd.DataFrame({"a": [1e200] * 5, "r": [1.0] * 5})
    r = verify_formula_text(overflow, "[r] = [a] * [a]", {}, cfg.thresholds)
    assert (r.match_rate, r.n_invalid) == (0.0, 5)
    df = pd.DataFrame({"a": ["inf", 1, 2, 3, 4, 5, np.inf], "r": [1.0, 2, 4, 6, 8, 10, 1]})
    r = verify_formula_text(df, "[r] = 2 * [a]", {}, cfg.thresholds)
    assert (r.match_rate, r.n_rows, r.n_invalid) == (1.0, 5, 2)


def test_a_text_value_in_a_number_column_does_not_crash(cfg):
    df = pd.DataFrame({"a": ["1", "2", "three", "4", "5", "6"], "b": [2, 4, 6, 8, 10, 12]})
    r = verify_formula_text(df, "[b] = 2 * [a]", {}, cfg.thresholds)
    assert (r.match_rate, r.n_rows, r.n_invalid) == (1.0, 5, 1)


@pytest.mark.parametrize("text,why", [
    ("[Revenue] = [Ghost] * 2", "unknown column"),
    ("[Revenue] = [Units] +* 2", "syntax"),
    ("[Revenue] = __import__('os').system('echo pwned')", "function call"),
    ("[Revenue] = [Units].__class__", "attribute"),
    ("[Revenue] = [Units][0]", "subscript"),
    ("[Revenue] = (lambda: 1)()", "lambda"),
    ("[Revenue] = sum([x for x in [Units]])", "comprehension"),
    ("[Revenue] = 'abc'", "string"),
    ("[Revenue] = [Units] if [Cost] else 0", "conditional expression"),
    ("[Revenue] = open('x')", "unlisted function"),
    ("[Revenue] = __c0 * 2", "internal placeholder name"),
    ("[Revenue] = [Units] ** 99999", "exponentiation"),
    ("[Revenue] = 9 ** 9 ** 9", "exponentiation"),
    ("[Revenue] = [Units] * 1e300", "huge constant"),
    ("[Revenue] = [Units] * 1e999", "infinite constant"),
    ("[Revenue] = round([Units], [Cost])", "round digits not constant"),
    ("[Revenue] = years_between([Units] + 1, [Cost])", "date function on an expression"),
    ("[Revenue] = [Units] + " + " + ".join(["[Cost]"] * 80), "too long"),
    ("[Revenue] = " + " + ".join(["[Cost]"] * 30), "too many nodes"),
    ("[Revenue] = " + "-" * (MAX_DEPTH + 5) + "[Cost]", "too deep"),
])
def test_unsafe_or_unsupported_formulas_are_rejected_without_running(sales_df, cfg, no_code_execution,
                                                                      text, why):
    r = verify_formula_text(sales_df, text, {}, cfg.thresholds)
    assert r.rejected and r.detail.startswith("rejected formula"), why
    assert policy(r, cfg, stated=True) == (RuleTier.CONTRADICTED, DecisionStatus.REJECTED, Enforcement.NONE)


def test_non_text_expression_is_rejected(sales_df, cfg):
    assert verify_proposal(sales_df, formula(["[Revenue]"]), None, {}, cfg.thresholds).rejected


def test_duplicate_index_labels_keep_rows_aligned(sales_df, cfg):
    df = sales_df.set_axis([7, 7, 8, 8, 9, 9])
    assert verify_formula_text(df, "[Revenue] = [Units] * [Unit Price]", {}, cfg.thresholds).match_rate == 1.0
    df.iloc[1, df.columns.get_loc("Revenue")] = -1
    r = verify_formula_text(df, "[Revenue] = [Units] * [Unit Price]", {}, cfg.thresholds)
    assert r.match_rate == pytest.approx(5 / 6) and r.n_rows == 6


def test_formula_check_counts_add_up():
    df = pd.DataFrame({"a": [1.0, 2, 3, 4, 5, 6, None, "x", 1], "b": [1.0, 0, 1, 1, 1, 1, 1, 1, 1],
                       "r": [1.0, 9, 3, 4, 5, 0, 1, 1, 1]})
    c = check_formula(df, parse_formula("[r] = [a] / [b]", ["a", "b", "r"]))
    assert (c.n_rows, c.n_matched, c.n_undefined, c.n_unreadable, c.n_missing) == (7, 5, 1, 1, 1)
    assert c.n_rows + c.n_unreadable + c.n_missing == len(df)


# ================================================================ date relationships
def ages(dobs, ages_, **extra):
    return pd.DataFrame({"DOB": dobs, "Age": ages_, **extra})


@pytest.mark.parametrize("dob,expected", [
    ("1990-03-01", 35),        # birthday already passed this year
    ("1990-09-01", 34),        # birthday not yet reached
    ("1990-06-30", 35),        # reference date is the birthday
    ("2025-06-30", 0),         # born on the reference date
])
def test_completed_age(dob, expected):
    r = verify_age_from_dob(ages([dob] * 5, [expected] * 5), "DOB", "Age", REF)
    assert r.match_rate == 1.0
    wrong = verify_age_from_dob(ages([dob] * 5, [expected + 1] * 5), "DOB", "Age", REF)
    assert wrong.match_rate == 0.0                       # off-by-one is a mismatch, not tolerated


def test_reference_date_before_birth_is_invalid_not_a_match():
    r = verify_age_from_dob(ages(["2030-01-01"] * 5, [0] * 5), "DOB", "Age", REF)
    assert (r.match_rate, r.n_rows, r.n_invalid) == (0.0, 5, 5)


def test_missing_birth_and_reference_dates_are_skipped():
    df = ages(["1990-03-01"] * 5 + [None, ""], [35] * 7,
              seen=["2025-06-30"] * 4 + [None, "2025-06-30", "2025-06-30"])
    r = verify_age_from_dob(df, "DOB", "Age", ref_col="seen", min_rows=4)
    assert (r.match_rate, r.n_rows, r.n_missing, r.n_invalid) == (1.0, 4, 3, 0)


def test_malformed_and_ambiguous_dates_are_unreadable():
    df = ages(["1990-03-01"] * 5 + ["1990-02-30", "not a date", "03/04/1990", "1990", "March 1990", 19900301],
              [35] * 11)
    r = verify_age_from_dob(df, "DOB", "Age", REF)
    assert (r.match_rate, r.n_rows, r.n_invalid) == (1.0, 5, 6)


def test_ambiguous_day_month_uses_a_known_format_never_a_guess():
    only_ambiguous = ages(["03/04/1990"] * 5, [35] * 5)
    assert verify_age_from_dob(only_ambiguous, "DOB", "Age", REF).match_rate is None
    as_dmy = verify_age_from_dob(only_ambiguous, "DOB", "Age", REF, date_formats={"DOB": "%d/%m/%Y"})
    assert as_dmy.match_rate == 1.0                      # 3 April 1990 -> 35 on 30 June 2025
    with_evidence = ages(["03/04/1990"] * 4 + ["25/12/1990"], [35] * 4 + [34])
    assert verify_age_from_dob(with_evidence, "DOB", "Age", REF).match_rate == 1.0   # column proves d/m/Y
    conflicting = ages(["03/04/1990", "25/12/1990", "12/25/1990"] * 2, [35] * 6)
    assert verify_age_from_dob(conflicting, "DOB", "Age", REF).n_invalid == 6


def test_fixed_trusted_reference_date_through_the_proposal(cfg):
    df = ages(["1990-03-01", "1990-09-01"] * 3, [35, 34] * 3)
    for p in (age_rule(), age_rule(reference_date={"source": "document"}, operation="age_from_dates")):
        assert verify_proposal(df, p, REF, {}, cfg.thresholds).match_rate == 1.0
    assert verify_proposal(df, age_rule(), None, {}, cfg.thresholds).detail == "no reference date: untestable"


def test_each_row_uses_its_own_reference_date(cfg):
    df = ages(["1990-06-15"] * 6, [30, 31, 31, 34, 35, 35],
              assessment_date=["2021-01-01", "2021-06-15", "2022-06-14", "2024-12-31", "2025-06-15", "2026-01-01"])
    p = age_rule(reference_date={"column": "assessment_date"})
    r = verify_proposal(df, p, REF, {}, cfg.thresholds)    # the document date must NOT replace row dates
    assert (r.match_rate, r.n_rows) == (1.0, 6) and "assessment_date" in r.detail
    assert verify_proposal(df, age_rule(), REF, {}, cfg.thresholds).match_rate < 0.5


def test_document_reference_date_is_used_not_the_machine_clock(cfg):
    df = ages(["1990-03-01", "2000-12-31", "1980-01-01"] * 2, [29, 19, 40] * 2)
    r = verify_proposal(df, age_rule(), date(2020, 1, 1), {}, cfg.thresholds)
    assert r.match_rate == 1.0 and "2020-01-01" in r.detail


def test_current_date_rule_needs_an_injected_run_date(cfg):
    df = ages(["1990-03-01", "1990-12-01"] * 3, [36, 35] * 3)
    p = age_rule(reference_date={"source": "run_date"})
    assert verify_proposal(df, p, REF, {}, cfg.thresholds, run_date=date(2026, 10, 25)).match_rate == 1.0
    untestable = verify_proposal(df, p, REF, {}, cfg.thresholds)
    assert untestable.match_rate is None and "run date" in untestable.detail


@pytest.mark.parametrize("ref", [{"date": "2024-01-01"}, "2024-01-01", {"column": "Ghost"},
                                 {"column": "DOB"}, {"source": "today"}, {"source": "document", "x": 1}])
def test_untrusted_or_invalid_reference_dates_are_rejected(cfg, ref):
    df = ages(["1990-03-01"] * 5, [35] * 5)
    r = verify_proposal(df, age_rule(reference_date=ref), REF, {}, cfg.thresholds)
    assert r.rejected
    assert policy(r, cfg)[2] == Enforcement.NONE


def test_unsupported_date_relationships_are_rejected_not_enforced(cfg):
    df = ages(["1990-03-01"] * 5, [35] * 5)
    for p in (age_rule(unit="days"), age_rule(operation="months_between"),
              Proposal(kind="derived_from", columns=["Age"], params={"unit": "years"}),
              age_rule(output_column="DOB")):
        r = verify_proposal(df, p, REF, {}, cfg.thresholds)
        assert r.rejected and policy(r, cfg)[2] == Enforcement.NONE


@pytest.mark.parametrize("ref,mar1,feb28", [
    (date(2023, 2, 28), 22, 23), (date(2023, 3, 1), 23, 23), (date(2024, 2, 28), 23, 23),
    (date(2024, 2, 29), 24, 24),
])
def test_leap_day_birthday_policy(ref, mar1, feb28):
    birth = pd.Series(pd.to_datetime(["2000-02-29"]))
    at = pd.Series(pd.to_datetime([ref]))
    assert completed_years(birth, at)[0] == mar1
    assert completed_years(birth, at, "feb28")[0] == feb28
    df = ages(["2000-02-29"] * 5, [mar1] * 5)
    assert verify_age_from_dob(df, "DOB", "Age", ref).match_rate == 1.0


def test_timezone_policy_for_aware_timestamps():
    late_evening = pd.Series(pd.to_datetime(["2025-06-30 23:30"] * 5)).dt.tz_localize("America/New_York")
    df = ages(["1990-07-01"] * 5, [35] * 5, seen=late_evening)
    assert verify_age_from_dob(df, "DOB", "Age", ref_col="seen").match_rate == 1.0          # UTC: 1 July
    local = verify_age_from_dob(df, "DOB", "Age", ref_col="seen", tz="America/New_York")   # local: 30 June
    assert local.match_rate == 0.0
    mixed = pd.Series(["2025-06-30T23:30:00-05:00", "2025-07-01", "2025-07-01 08:00"])
    dates, bad = to_calendar_dates(mixed)
    assert not bad.any() and (dates == pd.Timestamp("2025-07-01")).all()


def test_too_few_valid_rows_is_untestable(cfg):
    r = verify_age_from_dob(ages(["1990-03-01"] * 3, [35] * 3), "DOB", "Age", REF)
    assert r.match_rate is None and tier_from_result(r, cfg.thresholds) == RuleTier.SEMANTIC   # D-03
    broken = verify_age_from_dob(ages(["1990-03-01"] * 3 + ["junk"] * 4, [35] * 7), "DOB", "Age", REF)
    assert broken.match_rate is None and broken.n_invalid == 4
    assert policy(broken, cfg) == (RuleTier.UNKNOWN, DecisionStatus.NEEDS_REVIEW, Enforcement.NONE)


def test_mixture_of_matching_mismatching_and_invalid_rows():
    df = ages(["1990-03-01"] * 5 + ["1990-03-01", "garbage", "2030-01-01"], [35] * 5 + [40, 35, 35])
    r = verify_age_from_dob(df, "DOB", "Age", REF)
    assert (r.n_rows, r.n_invalid) == (7, 2)             # 'garbage' skipped; 2030 is tested but undefined
    assert r.match_rate == pytest.approx(5 / 7)
    assert "5/7" in r.detail and "garbage" not in r.detail   # counts only, never raw values


def test_years_between_formula_uses_calendar_arithmetic(cfg):
    df = pd.DataFrame({"DOB": ["2001-03-01"] * 5, "Ref": ["2002-03-01"] * 5, "Age": [1] * 5})
    r = verify_formula_text(df, "[Age] = years_between([Ref], [DOB])", {}, cfg.thresholds)
    assert r.match_rate == 1.0                           # floor(365 days / 365.25) would say 0


# ================================================================ classification and integration
def test_rejected_formula_is_never_enforced_even_if_stated_in_text(sales_df, cfg):
    r = verify_formula_text(sales_df, "[Revenue] = [Units].real", {}, cfg.thresholds)
    for grade in ExtractionGrade:
        assert policy(r, cfg, stated=True, grade=grade)[2] == Enforcement.NONE


def test_untestable_because_of_invalid_rows_is_never_hard(cfg):
    df = pd.DataFrame({"a": ["x"] * 8, "b": [1.0] * 8})
    r = verify_formula_text(df, "[b] = 2 * [a]", {}, cfg.thresholds)
    assert r.match_rate is None and r.n_invalid == 8
    assert policy(r, cfg)[2] == Enforcement.NONE
    assert policy(r, cfg, stated=True)[2] == Enforcement.NONE


def test_doubtful_extraction_still_caps_enforcement(orders_df, cfg):
    p = formula("[total_amount] = [quantity] * [unit_price] * (1 - [discount_pct] / 100) + [shipping]")
    r = verify_proposal(orders_df, p, None, {}, cfg.thresholds)
    assert policy(r, cfg, grade=ExtractionGrade.DOUBTFUL) == (
        RuleTier.HYPOTHESIZED, DecisionStatus.NEEDS_REVIEW, Enforcement.NONE)


@pytest.mark.parametrize("p", [
    Proposal(kind="determines", columns=["Ghost", "Region"]),
    Proposal(kind="determines", columns=["Region", "Region"]),
    Proposal(kind="ordering", columns=["Ref"]),
    Proposal(kind="ordering", columns=["Ref", "Region"], params={"min_gap_days": "ten"}),
    Proposal(kind="conditional", columns=["Region", "Units"], params={"eq": ["North"]}),
])
def test_malformed_proposals_are_rejected_without_crashing(sales_df, cfg, p):
    r = verify_proposal(sales_df, p, REF, {}, cfg.thresholds)
    assert r.rejected and policy(r, cfg)[2] == Enforcement.NONE


def test_ordering_survives_a_malformed_date():
    df = pd.DataFrame({"start": ["2020-01-01"] * 5 + ["soon"], "end": ["2021-01-01"] * 6})
    r = verify_ordering(df, "start", "end")
    assert (r.match_rate, r.n_rows, r.n_invalid) == (1.0, 5, 1)


def test_existing_kinds_still_dispatch(sales_df, cfg):
    p = Proposal(kind="determines", columns=["Ref", "Region"])
    assert verify_proposal(sales_df, p, REF, {}, cfg.thresholds).detail == "no repeated keys: untestable"
