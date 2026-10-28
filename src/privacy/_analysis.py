# src/privacy/_analysis.py
"""Sensitivity Classes, the Stricter-of Rule, the privacy policy table and the Label Map (M4.1, M4.2).

Who decides the Sensitivity Class (decide_risk):
  * HARD evidence is measured on the values by Python (emails, phone numbers). Only an LLM label with
    a reason and confidence >= llm_override_confidence (0.90) replaces it.
  * WEAK evidence is a guess from the header words or an ID-like shape. The LLM, which reads what
    the column means in any domain (finance, biology ...), REPLACES weak evidence when it gives a
    reason and its confidence is at least the trust threshold - so it may lower a keyword guess.
  * The LLM may always RAISE risk. With no usable LLM label, the stricter of the rule labels wins.
A policy turns the class into a generation method and a Noise Budget; the budget jitters fitted
parameters and is never added to source values.
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

# LLM-off fallback only: whole header WORDS (so 'Shipping' is not 'pin', 'Capacity' is not 'city').
DIRECT_HEADERS = ("name", "email", "phone", "mobile", "address", "ssn", "aadhaar", "passport",
                  "account", "patient")
QUASI_HEADERS = ("dob", "birth", "zip", "zipcode", "pin", "pincode", "postal", "postcode", "city",
                 "gender", "sex", "age")
SENSITIVE_HEADERS = ("salary", "income", "diagnosis", "medical", "credit", "balance", "disease")
EMAIL_RE = re.compile(r"^[^@ ]+@[^@ ]+[.][a-z]{2,}$", re.I)
PHONE_RE = re.compile(r"^[+]?[0-9][0-9 ()-]{7,}$")
DATE_LIKE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}$")
ID_RE = re.compile(r"^[A-Za-z]{1,4}[-_]?[0-9]{2,}$")
WORD_RE = re.compile(r"[A-Za-z]+")


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


def _is_phone(v: str) -> bool:
    return bool(PHONE_RE.match(v)) and not DATE_LIKE_RE.match(v) and sum(c.isdigit() for c in v) >= 8


def _header_words(column: str) -> set[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(column))          # camelCase -> camel Case
    return {w.lower() for w in WORD_RE.findall(spaced)}


def detect_evidence(column: str, values: pd.Series) -> tuple[SensitivityClass, str, str]:
    """Rule detector -> (class, reason, strength). 'hard' = measured on the values; 'weak' = a guess
    from the header words or an ID-like shape; 'none' = nothing matched."""
    words = _header_words(column)
    vals = values.dropna().astype(str).str.strip()
    vals = vals[vals != ""]
    if _share(vals, EMAIL_RE) >= 0.8:
        return SensitivityClass.DIRECT_IDENTIFIER, "email addresses in the values", "hard"
    if len(vals) and float(vals.map(_is_phone).mean()) >= 0.8:
        return SensitivityClass.DIRECT_IDENTIFIER, "phone numbers in the values", "hard"
    if len(vals) and vals.nunique() / len(vals) >= 0.95 and _share(vals, ID_RE) >= 0.8:
        return SensitivityClass.DIRECT_IDENTIFIER, "unique identifier-shaped values", "weak"
    for keywords, cls, what in ((DIRECT_HEADERS, SensitivityClass.DIRECT_IDENTIFIER, "a direct identifier"),
                                (QUASI_HEADERS, SensitivityClass.QUASI_IDENTIFIER, "a quasi-identifier"),
                                (SENSITIVE_HEADERS, SensitivityClass.SENSITIVE, "a sensitive value")):
        if words & set(keywords):
            return cls, f"header word suggests {what}", "weak"
    return SensitivityClass.LOW_RISK, "no rule matched", "none"


def merge_risk(llm_label: SensitivityClass | None, rule_label: SensitivityClass) -> SensitivityClass:
    """Stricter-of Rule. 'derived' is a method, not a risk, so an LLM 'derived' adds no risk."""
    llm = SensitivityClass.LOW_RISK if llm_label in (None, SensitivityClass.DERIVED) else llm_label
    return max((llm, rule_label), key=RISK_RANK.__getitem__)


def decide_risk(rule_label: SensitivityClass, strength: str, llm_label: SensitivityClass | None,
                llm_reason: str = "", llm_confidence: float | None = None,
                trust: float = 0.70, override: float = 0.90) -> tuple[SensitivityClass, str]:
    """-> (final class, who decided). The LLM needs a reason. It may always raise risk; at `trust` it
    replaces a header/shape guess; at `override` it replaces even what Python measured on the values."""
    llm = None if llm_label in (None, SensitivityClass.DERIVED) else llm_label
    confidence = llm_confidence or 0.0
    reasoned = llm is not None and bool(llm_reason.strip())
    if strength == "hard":
        if reasoned and confidence >= override:
            return llm, f"LLM reasoning overrode measured evidence (confidence {confidence:.2f})"
        return merge_risk(llm, rule_label), "values (hard evidence; LLM not confident enough to override)"
    if reasoned and confidence >= trust:
        return llm, "LLM reasoning (replaces the header/shape guess)"
    return merge_risk(llm, rule_label), "stricter of rule and LLM (LLM label not trusted enough to lower)"


def normalise_text(value) -> str:
    """For comparing names/addresses: case, spacing and punctuation do not matter."""
    return " ".join(re.sub(r"[^\w\s]", " ", str(value).lower()).split())


def exempt_columns(df: pd.DataFrame, preserved) -> set[str]:
    """The Entity Name, its address and branding are preserved, never regenerated (decision D-07).

    `preserved` is the list of protected values (or one entity name). Exemption is decided by the
    VALUES, not by a header word: a column of customer names is not the Entity Name just because its
    header says 'Company'.
    """
    items = [preserved] if isinstance(preserved, str) else list(preserved or [])
    protected = {normalise_text(v) for v in items if v and str(v).strip()}
    out: set[str] = set()
    if not protected:
        return out
    for col in df.columns:
        vals = df[col].dropna().map(normalise_text)
        if len(vals) and vals.isin(protected).mean() >= 0.8:
            out.add(str(col))
    return out


def leaked_values(synthetic: pd.Series, source: pd.Series) -> set[str]:
    return set(synthetic.dropna().astype(str)) & set(source.dropna().astype(str))


RELABEL_CLASSES = {SensitivityClass.DIRECT_IDENTIFIER, SensitivityClass.QUASI_IDENTIFIER}


def plan_table(table_id: str, df: pd.DataFrame, labels: dict[str, SensitivityClass],
               importance: dict[str, str], derived_cols: set[str], exempt_cols: set[str],
               reasons: dict[str, str] | None = None, confidence: dict[str, float] | None = None,
               trust: float = 0.70, override: float = 0.90) -> list[PrivacyDecision]:
    """One Privacy Decision per column: class, method, Noise Budget, importance, exemption, rationale.
    Text columns that identify people or organisations are marked `relabel` (Label Map)."""
    reasons, confidence = reasons or {}, confidence or {}
    decisions: list[PrivacyDecision] = []
    for col in df.columns:
        column = str(col)
        rule_label, why, strength = detect_evidence(column, df[col])
        llm_label = labels.get(column)
        final, decided_by = decide_risk(rule_label, strength, llm_label, reasons.get(column, ""),
                                        confidence.get(column), trust, override)
        key = SensitivityClass.DERIVED if column in derived_cols else final
        policy = POLICY[key]
        exempt = column in exempt_cols
        is_text = not (pd.api.types.is_numeric_dtype(df[col]) or pd.api.types.is_datetime64_any_dtype(df[col]))
        llm_part = (f"{llm_label.value} ({reasons[column]})" if llm_label and reasons.get(column)
                    else llm_label.value if llm_label else "none")
        decisions.append(PrivacyDecision(
            table_id=table_id, column=column, risk=key, rule_label=rule_label, llm_label=llm_label,
            rule_strength=strength, relabel=bool(is_text and not exempt and key in RELABEL_CLASSES),
            method="preserve" if exempt else policy.method,
            noise=None if exempt else policy.noise,
            importance=importance.get(column, "medium"), exempt=exempt,
            rationale=f"rule ({strength}): {why}; llm: {llm_part}; decided by {decided_by}"
                      + ("; keeps the source shape, so one known real value lets a reader estimate the others"
                         " (accepted for business metrics)" if policy.method == "trend_preserving" and not exempt
                         else "")))
    return decisions


def jitter(value: float, rel: float, rng: np.random.Generator) -> float:
    return float(value * (1 + rng.uniform(-rel, rel)))


class Relabeler:
    """Applies the Privacy Plan's relabelling during generation. One Label Map per relabelled column,
    filled from the plan's checked fake pool; a real value already mapped in ANY column maps to the
    same fake everywhere, so a customer name that appears in two tables stays one (fake) customer."""

    def __init__(self, plan, forbidden=frozenset()):
        self.preserved = list(plan.preserved_values)
        self.forbidden = set(forbidden) | set(self.preserved)
        self.columns = {f"{d.table_id}.{d.column}" for d in plan.decisions if d.relabel}
        self.pools = dict(plan.label_pools)
        self.seeds = dict(getattr(plan, "label_maps", {}) or {})          # E-35: fabricated pairs
        self.kept = dict(getattr(plan, "kept_labels", {}) or {})          # E-39: standard vocabulary
        self._seed_norm: dict[str, dict[str, str]] = {}
        self.maps: dict[str, LabelMap] = {}
        self.shared: dict[str, str] = {}
        self._used: set[str] = set()       # one set for every column: two real values never share a fake

    def wants(self, table_id: str, column: str) -> bool:
        node = f"{table_id}.{column}"
        return node in self.columns or node in self.seeds

    def get(self, table_id: str, column: str, value):
        node = f"{table_id}.{column}"
        if node not in self.columns:                   # E-35 pairs only: every other cell stays as it is
            if value is None or (isinstance(value, float) and np.isnan(value)):
                return value
            return self._seeded(node).get(normalise_text(value), value)
        return self._identifying(table_id, column, value)

    def _seeded(self, node: str) -> dict[str, str]:
        if node not in self._seed_norm:
            self._seed_norm[node] = {normalise_text(k): v for k, v in self.seeds.get(node, {}).items()}
        return self._seed_norm[node]

    def _identifying(self, table_id: str, column: str, value):
        if value is None or (isinstance(value, float) and np.isnan(value)) or str(value).strip() == "":
            return value
        node = f"{table_id}.{column}"
        key = normalise_text(value)
        if key in self.shared:
            return self.shared[key]
        if node not in self.maps:
            self.maps[node] = LabelMap(exempt=self.preserved + list(self.kept.get(node, [])),
                                       forbidden=self.forbidden, seed=self.seeds.get(node),
                                       pool=self.pools.get(node, []),
                                       fallback_prefix=(str(column) if len(str(column)) <= 24 and not any(
                                           ch.isdigit() or ch in ":/" for ch in str(column)) else "Item"))
            self.maps[node]._used = self._used
        new = self.maps[node].get(str(value))
        if new != value:
            self.shared[key] = new
        return new


class LabelMap:
    """Same source label -> same fresh label in every table.

    A fresh label never equals ANY value in `forbidden` (pass every source value of the document) and
    is never reused. Labels come first from `pool` (LLM-made fakes, already checked), then from
    `factory`, then from a numbered fallback, so it always finishes. `exempt` values (the entity name
    and address) are returned unchanged."""

    def __init__(self, factory=None, rng: np.random.Generator | None = None, exempt=frozenset(),
                 forbidden=frozenset(), pool=(), fallback_prefix: str = "Item", max_tries: int = 200,
                 seed: dict[str, str] | None = None):
        self.factory, self.rng = factory, rng
        self.exempt = {normalise_text(v) for v in exempt}
        self.forbidden = {normalise_text(v) for v in forbidden}
        self.pool, self.prefix, self.max_tries = list(pool), fallback_prefix, max_tries
        self.map: dict[str, str] = {}
        self._used: set[str] = set()
        self._counter = 0
        self.seed = {normalise_text(k): v for k, v in (seed or {}).items()}   # checked pairs, used first

    def _free(self, candidate: str, source_label: str) -> bool:
        n = normalise_text(candidate)
        return bool(n) and n != normalise_text(source_label) and n not in self.forbidden and n not in self._used

    def _fresh(self, source_label: str) -> str:
        while self.pool:
            candidate = str(self.pool.pop(0))
            if self._free(candidate, source_label):
                return candidate
        if self.factory is not None:
            for _ in range(self.max_tries):
                candidate = str(self.factory(self.rng))
                if self._free(candidate, source_label):
                    return candidate
        while True:                                       # always ends: the counter only grows
            self._counter += 1
            candidate = f"{self.prefix} {self._counter}"
            if self._free(candidate, source_label):
                return candidate

    def get(self, source_label: str) -> str:
        if normalise_text(source_label) in self.exempt:
            return source_label
        if source_label not in self.map:
            seeded = self.seed.get(normalise_text(source_label))
            new = seeded if seeded is not None and normalise_text(seeded) not in self._used                 else self._fresh(source_label)
            self._used.add(normalise_text(new))
            self.map[source_label] = new
        return self.map[source_label]
