# src/privacy/_analysis.py
"""Sensitivity Classes, the Stricter-of Rule, the privacy policy table and the Label Map (M4.1, M4.2).

The rule detector and the LLM both label a column; the final class is the stricter of the two, so the
model can raise risk but never lower it. A policy turns the class into a generation method and a Noise
Budget; the budget jitters fitted parameters and is never added to source values.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.contracts import SensitivityClass, PrivacyDecision

RISK_RANK = {SensitivityClass.LOW_RISK: 0, SensitivityClass.BUSINESS_METRIC: 1,
             SensitivityClass.SENSITIVE: 2, SensitivityClass.QUASI_IDENTIFIER: 3,
             SensitivityClass.DIRECT_IDENTIFIER: 4}

DIRECT_HEADERS = ("name", "email", "phone", "mobile", "address", "ssn", "aadhaar", "passport", "account")
QUASI_HEADERS = ("dob", "birth", "zip", "pin", "postal", "city", "gender")
SENSITIVE_HEADERS = ("salary", "income", "diagnosis", "medical", "credit", "balance")
EMAIL_RE = re.compile(r"^[^@ ]+@[^@ ]+[.][a-z]{2,}$", re.I)
PHONE_RE = re.compile(r"^[+]?[0-9][0-9 ()-]{7,}$")
ID_RE = re.compile(r"^[A-Za-z]{1,4}[-_]?[0-9]{2,}$")


@dataclass(frozen=True)
class Policy:
    method: str
    noise: float | None          # relative distortion budget; None = values fully regenerated


POLICY = {
    SensitivityClass.DIRECT_IDENTIFIER: Policy("regenerate", None),
    SensitivityClass.QUASI_IDENTIFIER: Policy("resample_new_distribution", 0.30),
    SensitivityClass.SENSITIVE: Policy("fitted_distribution", 0.12),
    SensitivityClass.BUSINESS_METRIC: Policy("trend_preserving", 0.30),
    SensitivityClass.LOW_RISK: Policy("category_resample", 0.05),
    SensitivityClass.DERIVED: Policy("derived", 0.0),
}


def _share(values: pd.Series, rx: re.Pattern) -> float:
    return float(values.map(lambda v: bool(rx.match(v))).mean()) if len(values) else 0.0


def detect_risk(column: str, values: pd.Series) -> tuple[SensitivityClass, str]:
    """Rule detector: what the values and the Column Header say, with no model involved."""
    header = str(column).lower()
    vals = values.dropna().astype(str)
    if _share(vals, EMAIL_RE) >= 0.8 or _share(vals, PHONE_RE) >= 0.8:
        return SensitivityClass.DIRECT_IDENTIFIER, "email or phone pattern in values"
    if len(vals) and vals.nunique() / len(vals) >= 0.95 and _share(vals, ID_RE) >= 0.8:
        return SensitivityClass.DIRECT_IDENTIFIER, "unique identifier-shaped values"
    if any(k in header for k in DIRECT_HEADERS):
        return SensitivityClass.DIRECT_IDENTIFIER, "header suggests a direct identifier"
    if any(k in header for k in QUASI_HEADERS):
        return SensitivityClass.QUASI_IDENTIFIER, "header suggests a quasi-identifier"
    if any(k in header for k in SENSITIVE_HEADERS):
        return SensitivityClass.SENSITIVE, "header suggests a sensitive value"
    return SensitivityClass.LOW_RISK, "no rule matched"


def merge_risk(llm_label: SensitivityClass | None, rule_label: SensitivityClass) -> SensitivityClass:
    """Stricter-of Rule. 'derived' is a method, not a risk, so an LLM 'derived' adds no risk."""
    llm = SensitivityClass.LOW_RISK if llm_label in (None, SensitivityClass.DERIVED) else llm_label
    return max((llm, rule_label), key=RISK_RANK.__getitem__)


def exempt_columns(df: pd.DataFrame, entity_name: str | None) -> set[str]:
    """The Entity Name and branding are preserved, never regenerated (decision D-07).

    Exemption is decided by the VALUES, not by a header word: a column of customer names is not the
    Entity Name just because its header says 'Company'.
    """
    out: set[str] = set()
    name = (entity_name or "").strip().lower()
    if not name:
        return out
    for col in df.columns:
        vals = df[col].dropna().astype(str).str.strip().str.lower()
        if len(vals) and (vals == name).mean() >= 0.8:
            out.add(str(col))
    return out


def leaked_values(synthetic: pd.Series, source: pd.Series) -> set[str]:
    return set(synthetic.dropna().astype(str)) & set(source.dropna().astype(str))


def plan_table(table_id: str, df: pd.DataFrame, labels: dict[str, SensitivityClass],
               importance: dict[str, str], derived_cols: set[str],
               exempt_cols: set[str]) -> list[PrivacyDecision]:
    """One Privacy Decision per column: class, method, Noise Budget, importance, exemption, rationale."""
    decisions: list[PrivacyDecision] = []
    for col in df.columns:
        column = str(col)
        rule_label, why = detect_risk(column, df[col])
        llm_label = labels.get(column)
        final = merge_risk(llm_label, rule_label)
        key = SensitivityClass.DERIVED if column in derived_cols else final
        policy = POLICY[key]
        exempt = column in exempt_cols
        decisions.append(PrivacyDecision(
            table_id=table_id, column=column, risk=key, rule_label=rule_label, llm_label=llm_label,
            method="preserve" if exempt else policy.method,
            noise=None if exempt else policy.noise,
            importance=importance.get(column, "medium"), exempt=exempt,
            rationale=f"rule: {why}; llm: {llm_label.value if llm_label else 'none'}"))
    return decisions


def exact_row_overlap(synthetic: pd.DataFrame, source: pd.DataFrame, cols: list[str]) -> int:
    return len(synthetic[cols].astype(str).merge(source[cols].astype(str).drop_duplicates(), on=cols))


def rare_combinations(df: pd.DataFrame, quasi_cols: list[str], k: int = 3) -> pd.Series:
    sizes = df.groupby(quasi_cols).size()
    return sizes[sizes < k]


def jitter(value: float, rel: float, rng: np.random.Generator) -> float:
    return float(value * (1 + rng.uniform(-rel, rel)))


class LabelMap:
    """Same source label -> same fresh label in every table; never equal to the source label."""

    def __init__(self, factory, rng: np.random.Generator, exempt: set[str] = frozenset()):
        self.factory, self.rng, self.exempt, self.map = factory, rng, set(exempt), {}

    def get(self, source_label: str) -> str:
        if source_label in self.exempt:
            return source_label
        if source_label not in self.map:
            new = self.factory(self.rng)
            while new == source_label or new in self.map.values():
                new = self.factory(self.rng)
            self.map[source_label] = new
        return self.map[source_label]
