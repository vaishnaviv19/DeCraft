# tests/inference/test_verifiers.py
import inspect

import numpy as np
import pandas as pd
import pytest

from src.contracts import DecisionStatus, Enforcement, ExtractionGrade, RuleTier
from src.inference._verifiers import (VerifyResult, classify_rule, tier_from_result, verify_age_from_dob,
                                      verify_conditional, verify_determines, verify_formula_text,
                                      verify_ordering)

REF = pd.Timestamp("2025-06-30")


@pytest.fixture
def person_df():
    dob = pd.to_datetime(["1990-03-01", "1985-11-20", "2000-01-15", "1975-07-04", "1995-09-09", "1988-12-31"])
    days = pd.Series(REF - dob).dt.days
    return pd.DataFrame({"DOB": dob, "Age": np.floor(days / 365.25).astype(int)})


def test_age_from_dob_confirmed(person_df, cfg):
    r = verify_age_from_dob(person_df, "DOB", "Age", REF)
    assert r.match_rate == 1.0 and r.n_rows == 6
    assert tier_from_result(r, cfg.thresholds) == RuleTier.STRONGLY_INFERRED


def test_llm_formula_confirmed_and_false_formula_contradicted(sales_df, cfg):
    th = cfg.thresholds
    ok = verify_formula_text(sales_df, "[Revenue] = [Units] * [Unit Price]", {}, th)
    bad = verify_formula_text(sales_df, "[Revenue] = [Cost] / 10", {}, th)
    assert tier_from_result(ok, th) == RuleTier.STRONGLY_INFERRED
    assert tier_from_result(bad, th) == RuleTier.CONTRADICTED


def test_unparseable_formula_is_contradicted(sales_df, cfg):
    r = verify_formula_text(sales_df, '[Revenue] = __import__("os")', {}, cfg.thresholds)
    assert tier_from_result(r, cfg.thresholds) == RuleTier.CONTRADICTED


def test_three_rows_stay_semantic(cfg):
    df = pd.DataFrame({"Pin": ["1", "1", "2"], "City": ["A", "A", "B"]})
    assert tier_from_result(verify_determines(df, "Pin", "City"), cfg.thresholds) == RuleTier.SEMANTIC


def test_conflicting_dependency_is_contradicted(cfg):
    pins = [str(i) for i in range(6) for _ in range(2)]
    cities = []
    for i in range(6):
        cities += ["X", "X"] if i < 1 else ["X", "Y"]
    r = verify_determines(pd.DataFrame({"Pin": pins, "City": cities}), "Pin", "City")
    assert tier_from_result(r, cfg.thresholds) == RuleTier.CONTRADICTED


@pytest.mark.parametrize("rate,rows,expected", [
    (0.95, 10, RuleTier.STRONGLY_INFERRED), (0.94, 10, RuleTier.HYPOTHESIZED),
    (0.80, 10, RuleTier.HYPOTHESIZED), (0.79, 10, RuleTier.UNKNOWN), (0.50, 10, RuleTier.UNKNOWN),
    (0.49, 10, RuleTier.CONTRADICTED), (None, 10, RuleTier.SEMANTIC), (1.0, 3, RuleTier.SEMANTIC),
])
def test_tier_boundaries(cfg, rate, rows, expected):
    assert tier_from_result(VerifyResult(rate, rows, ""), cfg.thresholds) == expected


@pytest.mark.parametrize("rate,rows,text,grade,tier,status,enf", [
    (1.0, 10, False, ExtractionGrade.GOOD, RuleTier.STRONGLY_INFERRED, DecisionStatus.ACCEPTED, Enforcement.HARD),
    (None, 3, False, ExtractionGrade.GOOD, RuleTier.SEMANTIC, DecisionStatus.ACCEPTED, Enforcement.HARD),
    (0.85, 10, False, ExtractionGrade.GOOD, RuleTier.HYPOTHESIZED, DecisionStatus.NEEDS_REVIEW, Enforcement.NONE),
    (0.85, 10, True, ExtractionGrade.GOOD, RuleTier.EXPLICIT, DecisionStatus.ACCEPTED, Enforcement.HARD),
    (0.30, 10, True, ExtractionGrade.GOOD, RuleTier.CONTRADICTED, DecisionStatus.REJECTED, Enforcement.NONE),
    (1.0, 10, False, ExtractionGrade.DOUBTFUL, RuleTier.HYPOTHESIZED, DecisionStatus.NEEDS_REVIEW, Enforcement.NONE),
    (0.60, 10, False, ExtractionGrade.GOOD, RuleTier.UNKNOWN, DecisionStatus.NEEDS_REVIEW, Enforcement.NONE),
])
def test_classify_rule_policy(cfg, rate, rows, text, grade, tier, status, enf):
    got = classify_rule(VerifyResult(rate, rows, "x"), cfg.thresholds, text, grade)
    assert got[:3] == (tier, status, enf) and got[3]


def test_high_llm_confidence_alone_never_enforces(cfg):
    """A confident but unverified proposal cannot be enforced: the policy has no confidence input."""
    assert "confidence" not in inspect.signature(classify_rule).parameters


def test_ordering_and_conditional():
    df = pd.DataFrame({"dob": pd.to_datetime(["1990-01-01"] * 6),
                       "join": pd.to_datetime(["2012-01-01"] * 6),
                       "status": ["Closed"] * 6,
                       "closed_on": pd.to_datetime(["2020-01-01"] * 6)})
    assert verify_ordering(df, "dob", "join", min_gap_days=16 * 365).match_rate == 1.0
    assert verify_conditional(df, "status", "Closed", "closed_on").match_rate == 1.0
