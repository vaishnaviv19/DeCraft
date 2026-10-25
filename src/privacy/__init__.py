# src/privacy/__init__.py  -- PUBLIC INTERFACE of the privacy module (deep module)
"""Privacy Decisions before generation and the privacy audit afterwards.

`plan_privacy` applies the Stricter-of Rule (rule detector vs LLM label), the policy table and the
Entity Name exemption. `audit_privacy` checks identifier reuse, Value Distance, Headline Distance,
quasi-identifier row matches and rare combinations, and always states the standard limitations.
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from src.config import Config
from src.contracts import (Enforcement, PrivacyPlan, PrivacyReport, RuleKind, SensitivityClass,
                           Severity, SourceDocument, Violation)
from src.contracts.dataset import CanonicalDataset
from src.core.frames import table_frame
from src.core.values import parse_number
from src.privacy._analysis import exempt_columns, leaked_values, plan_table
from src.privacy._distance import (headline_violations, quasi_row_matches,
                                   rare_combination_violations, value_distance_violations)

__all__ = ["plan_privacy", "audit_privacy"]

CHECKS_RUN = ["identifier_reuse", "value_distance", "quasi_identifier_match",
              "rare_combination", "headline_distance"]


def _profiles_by_table(report) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for profile in report.profiles:
        out[profile.table_id].append(profile)
    return out


def _raw_frame(table) -> pd.DataFrame:
    """Fallback when no Field Profiles are available: numbers as displayed, no Scale applied."""
    cols = {}
    for j, header in enumerate(table.header):
        values = [row[j] if j < len(row) else None for row in table.raw_cells]
        parsed = [parse_number(v) for v in values]
        cols[str(header)] = (pd.Series(parsed, dtype="float64") if any(p is not None for p in parsed)
                             else pd.Series([None if v is None or str(v).strip() == "" else str(v).strip()
                                             for v in values], dtype=object))
    return pd.DataFrame(cols, index=range(len(table.raw_cells)))


def _label_maps(report, table_id: str) -> tuple[dict[str, SensitivityClass], dict[str, str]]:
    labels, importance = {}, {}
    prefix = f"{table_id}."
    for key, value in report.sensitivity_labels.items():
        if key.startswith(prefix):
            try:
                labels[key[len(prefix):]] = SensitivityClass(value)
            except ValueError:
                continue
    for key, value in report.column_importance.items():
        if key.startswith(prefix):
            importance[key[len(prefix):]] = value
    return labels, importance


def plan_privacy(doc: SourceDocument, report, cfg: Config) -> PrivacyPlan:
    """Stricter-of(rule detector, LLM label); derived columns -> 'derived'; Entity Name/branding exempt;
    tiny tables listed for the jitter policy."""
    derived = {f"{rule.table_id}.{rule.formula.output}" for rule in report.rules
               if rule.kind is RuleKind.FORMULA and rule.enforcement is Enforcement.HARD and rule.formula}
    profiles = _profiles_by_table(report)
    decisions, tiny = [], []
    for table in doc.tables:
        table_profiles = profiles.get(table.table_id)
        if not table_profiles:
            continue                                  # a 'bad' table has no profiles and is skipped
        frame, _ = table_frame(table, table_profiles)
        labels, importance = _label_maps(report, table.table_id)
        derived_here = {key.split(".", 1)[1] for key in derived if key.startswith(f"{table.table_id}.")}
        decisions.extend(plan_table(table.table_id, frame, labels, importance, derived_here,
                                    exempt_columns(frame, doc.entity_name)))
        if len(frame) <= cfg.thresholds.tiny_table_max_rows:
            tiny.append(table.table_id)
    return PrivacyPlan(decisions=decisions, tiny_tables=tiny)


def audit_privacy(doc: SourceDocument, data: CanonicalDataset, plan: PrivacyPlan,
                  cfg: Config) -> PrivacyReport:
    """Post-generation checks. Never mutates the dataset; every finding is a Violation."""
    th = cfg.thresholds
    decisions: dict[str, dict] = defaultdict(dict)
    for decision in plan.decisions:
        decisions[decision.table_id][decision.column] = decision

    profiles = defaultdict(list)
    for profile in data.source_profiles:
        profiles[profile.table_id].append(profile)

    violations: list[Violation] = []
    source_facts: dict[str, float] = {}
    for table in doc.tables:
        synthetic = data.tables.get(table.table_id)
        if synthetic is None:
            continue
        table_profiles = profiles.get(table.table_id)
        source = table_frame(table, table_profiles)[0] if table_profiles else _raw_frame(table)
        table_decisions = decisions.get(table.table_id, {})

        for column, decision in table_decisions.items():
            if decision.risk is SensitivityClass.DIRECT_IDENTIFIER and not decision.exempt \
                    and column in synthetic.columns and column in source.columns:
                leaked = leaked_values(synthetic[column], source[column])
                if leaked:
                    violations.append(Violation(
                        check="identifier_reuse", severity=Severity.ERROR, table_id=table.table_id,
                        column=column,
                        message=f"{len(leaked)} generated values reuse a source identifier",
                        repairable=True))

        numeric = [c for c, d in table_decisions.items()
                   if c in source.columns and c in synthetic.columns
                   and pd.api.types.is_numeric_dtype(source[c]) and not d.exempt
                   and d.risk not in (SensitivityClass.DIRECT_IDENTIFIER, SensitivityClass.DERIVED)]
        key = next(iter(data.key_columns.get(table.table_id, [])), None)
        violations += value_distance_violations(table.table_id, source, synthetic, key, numeric,
                                                th.min_value_distance)

        quasi = [c for c, d in table_decisions.items()
                 if d.risk is SensitivityClass.QUASI_IDENTIFIER
                 and c in source.columns and c in synthetic.columns]
        violations += quasi_row_matches(table.table_id, source, synthetic, quasi)
        violations += rare_combination_violations(table.table_id, synthetic, quasi, th)

        for column in source.columns:
            if pd.api.types.is_numeric_dtype(source[column]):
                source_facts[f"{table.table_id}.{column}:sum"] = float(source[column].sum())

    synthetic_facts = {fact.source: float(fact.value) for fact in data.facts.values()
                       if fact.kind == "number" and isinstance(fact.value, (int, float))}
    violations += headline_violations(source_facts, synthetic_facts, th.min_headline_distance)

    return PrivacyReport(ok=not any(v.severity is Severity.ERROR for v in violations),
                         checks_run=list(CHECKS_RUN), violations=violations)
