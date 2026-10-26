# src/privacy/__init__.py  -- PUBLIC INTERFACE of the privacy module (deep module)
"""Privacy Decisions before generation and the privacy audit afterwards.

`plan_privacy` decides each column's Sensitivity Class (hard evidence on the values cannot be
lowered; a reasoned, confident LLM label replaces header/shape guesses; the LLM may always raise
risk), applies the policy table, protects the entity name and address everywhere, and asks the
Label Fabricator (E-35) for checked replacement labels. `audit_privacy` checks identifier reuse, Value Distance, Headline Distance,
quasi-identifier row matches and rare combinations, and always states the standard limitations.
"""
from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace

import numpy as np
import pandas as pd

from src.config import Config
from src.contracts import (Enforcement, PrivacyPlan, PrivacyReport, RuleKind, SensitivityClass,
                           Severity, SourceDocument, Violation)
from src.contracts.dataset import CanonicalDataset
from src.core.facts import compute_facts
from src.core.frames import table_frame
from src.core.rows import label_positions, norm_label, statement_label_column
from src.core.values import parse_number
from src.core.originality import name_words, source_proper_nouns
from src.core.periods import parse_period
from src.privacy._agents import advise_methods, apply_advice, find_entity
from src.privacy._labels import LabelColumn, fabricate_labels
from src.privacy._analysis import (Relabeler, exempt_columns, leaked_values, normalise_text,
                                   plan_table)
from src.privacy._distance import (headline_violations, quasi_row_matches,
                                   rare_combination_violations, value_distance_violations)

__all__ = ["plan_privacy", "audit_privacy", "Relabeler", "document_values", "is_word_label"]

STRUCTURAL_LABELS = ("total", "subtotal", "sub total", "grand total")   # kept by generation
STRUCTURAL_NUMBERS = {"100", "100.0", "100.00", "100%", "100.0%"}        # a share total is not data

CHECKS_RUN = ["identifier_reuse", "label_reuse", "text_number_reuse", "value_distance", "value_distance_displayed",
              "quasi_identifier_match", "rare_combination", "headline_distance"]


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


def _per_table(mapping: dict, table_id: str) -> dict:
    prefix = f"{table_id}."
    return {key[len(prefix):]: value for key, value in mapping.items() if key.startswith(prefix)}


def _label_maps(report, table_id: str) -> tuple[dict[str, SensitivityClass], dict[str, str]]:
    labels = {}
    for column, value in _per_table(report.sensitivity_labels, table_id).items():
        try:
            labels[column] = SensitivityClass(value)
        except ValueError:
            continue
    return labels, _per_table(report.column_importance, table_id)


def _strong_correlations(frame: pd.DataFrame, min_rows: int = 8, min_rho: float = 0.5) -> list[dict]:
    numeric = frame.select_dtypes("number")
    if numeric.shape[1] < 2 or len(numeric.dropna()) < min_rows:
        return []
    rho = numeric.dropna().corr(method="spearman")
    pairs = [{"a": a, "b": b, "spearman": round(float(rho.loc[a, b]), 2)}
             for i, a in enumerate(rho.columns) for b in rho.columns[i + 1:]
             if abs(rho.loc[a, b]) >= min_rho]
    return sorted(pairs, key=lambda x: -abs(x["spearman"]))


HEADLINE_SUFFIXES = (".total", ".average", ".largest", ".latest")


def _headline(facts: dict) -> dict[str, float]:
    return {k: float(f.value) for k, f in facts.items()
            if f.kind == "number" and isinstance(f.value, (int, float)) and k.endswith(HEADLINE_SUFFIXES)}


def _as_displayed(source: pd.DataFrame, profiles: list) -> pd.DataFrame:
    """The source table as a reader saw it: each number column divided by its Scale and rounded."""
    out = source.copy()
    for prof in profiles:
        if prof.column in out.columns and pd.api.types.is_numeric_dtype(out[prof.column]):
            values = out[prof.column] / (prof.scale or 1.0)
            out[prof.column] = values.round(prof.decimals) if prof.decimals is not None else values
    return out


def _computed_rows(rules, table_id: str, source: pd.DataFrame) -> set[int]:
    """Rows that HARD row formulas compute (derived, like formula columns): no distance check."""
    out = set()
    for rule in rules:
        if rule.kind is RuleKind.ROW_FORMULA and rule.table_id == table_id and rule.formula is not None                 and rule.enforcement is Enforcement.HARD:
            label = rule.params.get("label_column") or (rule.columns[0] if rule.columns else None)
            if label in source.columns:
                out |= set(label_positions(source[label]).get(norm_label(rule.formula.output), []))
    return out


def document_values(doc: SourceDocument) -> set[str]:
    """Every real cell value of the document, normalised: a fake label may never equal one of them."""
    return {n for t in doc.tables for row in t.raw_cells for v in row
            if v is not None and (n := normalise_text(v))}


def entity_context(doc: SourceDocument, gateway=None, llm_enabled: bool = False
                   ) -> tuple[list[str], str | None]:
    """-> (protected values, locale). The entity name and address are never regenerated anywhere
    (D-07); a value found by the entity agent is kept only if it is written in the document. The
    locale ('Pune, India') only steers the style of fake values."""
    name, address, locale = doc.entity_name, doc.entity_address, None
    if llm_enabled and gateway is not None and gateway.enabled:
        found_name, found_address, locale = find_entity(" ".join(doc.text_blocks), gateway, name)
        name, address = name or found_name, address or found_address
    return [v.strip() for v in (name, address) if v and v.strip()], locale


def protected_values(doc: SourceDocument, gateway=None, llm_enabled: bool = False) -> list[str]:
    return entity_context(doc, gateway, llm_enabled)[0]


def plan_privacy(doc: SourceDocument, report, cfg: Config, gateway=None) -> PrivacyPlan:
    """One decision per column (see the module docstring); derived columns -> 'derived'; the entity
    name and address are exempt; tiny tables listed for the jitter policy; with the LLM on, every
    relabelled text column gets believable, checked fake values that fit the entity's locale."""
    llm_on = bool(cfg.llm_enabled and gateway is not None and gateway.enabled)
    trust = cfg.thresholds.llm_confidence_escalate
    rng = np.random.default_rng(cfg.seed)
    derived = {f"{rule.table_id}.{rule.formula.output}" for rule in report.rules
               if rule.kind is RuleKind.FORMULA and rule.enforcement is Enforcement.HARD and rule.formula}
    preserved, locale = entity_context(doc, gateway, llm_on)
    forbidden = document_values(doc) | {normalise_text(v) for v in preserved}
    profiles = _profiles_by_table(report)
    decisions, tiny, pools, warnings = [], [], {}, []
    label_columns: list[LabelColumn] = []
    for table in doc.tables:
        table_profiles = profiles.get(table.table_id)
        if not table_profiles:
            continue                                  # a 'bad' table has no profiles and is skipped
        frame, _ = table_frame(table, table_profiles)
        labels, importance = _label_maps(report, table.table_id)
        derived_here = {key.split(".", 1)[1] for key in derived if key.startswith(f"{table.table_id}.")}
        table_decisions = plan_table(
            table.table_id, frame, labels, importance, derived_here, exempt_columns(frame, preserved),
            _per_table(report.sensitivity_reasons, table.table_id),
            _per_table(report.sensitivity_confidence, table.table_id), trust,
            cfg.thresholds.llm_override_confidence)
        # keys with no digits to copy a format from ('Alpha', 'Beta'): fresh keys must be believable
        # fakes, so they go through the Label Map like identifying text
        for k, d in enumerate(table_decisions):
            values = frame[d.column].dropna().astype(str)
            if (d.column in report.key_columns.get(table.table_id, []) and not d.relabel and not d.exempt
                    and len(values) and values.str.contains(r"\d").mean() < 0.2):
                table_decisions[k] = d.model_copy(update={
                    "relabel": True, "rationale": d.rationale + "; key without a number format -> fake labels"})
        if llm_on:                                     # the old PrivacyAgent's advice, now via the gateway
            meanings = _per_table(report.column_meanings, table.table_id)
            texts = {d.column: not (pd.api.types.is_numeric_dtype(frame[d.column])
                                    or pd.api.types.is_datetime64_any_dtype(frame[d.column]))
                     for d in table_decisions}
            advice = advise_methods(table.table_id, table.caption, [
                {"column": d.column, "description": meanings.get(d.column, ""), "label": d.risk.value,
                 "method": d.method, "noise": d.noise, "type": "text" if texts[d.column] else "number",
                 "samples": [str(v) for v in frame[d.column].head(3).dropna()]}
                for d in table_decisions if not d.exempt and d.method != "derived"],
                _strong_correlations(frame), gateway, locale)
            table_decisions = [apply_advice(d, advice.get(d.column), texts[d.column], trust,
                                            cfg.thresholds.llm_override_confidence) for d in table_decisions]
        decisions.extend(table_decisions)
        if len(frame) <= cfg.thresholds.tiny_table_max_rows:
            tiny.append(table.table_id)
        label = next((p.column for p in table_profiles if p.physical_type == "text"), None)
        if statement_label_column(frame, label):
            warnings.append(f"{table.table_id}: statement table - numbers are scaled copies of the source "
                            "(one factor for the table), so one known real value reveals the factor")
        label_columns += _label_columns(table.table_id, frame, table_profiles, table_decisions, report)

    # E-35: one Label Fabricator for every text label of the document
    text = " ".join(doc.text_blocks)
    avoid = source_proper_nouns(text, keep=set(preserved) | ({doc.entity_name} if doc.entity_name else set()))
    made = fabricate_labels(label_columns, gateway if llm_on else None, forbidden, avoid,
                            name_words(text, doc.entity_name), preserved, locale, rng)
    pools.update(made.pools)
    warnings += made.warnings
    changed = {node for node, mapping in made.maps.items() if mapping}
    # identifying columns stay `relabel` (every value replaced); other columns only swap their
    # fabricated pairs (E-35) and leave non-label cells such as numbers stored as text alone
    decisions = [d.model_copy(update={"rationale": d.rationale + "; labels fabricated (E-35)"})
                 if f"{d.table_id}.{d.column}" in changed and not d.relabel else d for d in decisions]
    for d in decisions:
        node = f"{d.table_id}.{d.column}"
        if d.relabel and node not in made.maps and node not in pools:
            warnings.append(f"{node}: no fabricated labels, replacement labels will be numbered")
    return PrivacyPlan(decisions=decisions, tiny_tables=tiny, preserved_values=preserved,
                       label_pools=pools, label_maps={k: v for k, v in made.maps.items() if v},
                       kept_labels={k: v for k, v in made.kept.items() if v}, avoid=sorted(avoid),
                       locale=locale, warnings=warnings)


def is_word_label(value: str) -> bool:
    text = str(value).strip()
    return (sum(ch.isalpha() for ch in text) >= 2 and parse_number(text) is None
            and parse_period(text) is None)


def _label_columns(table_id, frame, profiles, decisions, report) -> list[LabelColumn]:
    """Every text column whose labels reach the output: not a period axis, not exempt, not preserved or
    derived, and not a key with a number format (those get fresh keys in the same format)."""
    types = {p.column: p.physical_type for p in profiles}
    meanings = _per_table(report.column_meanings, table_id)
    keys = set(report.key_columns.get(table_id, []))
    out = []
    for d in decisions:
        if d.exempt or d.method in ("preserve", "derived") or types.get(d.column) != "text":
            continue
        values = frame[d.column].dropna().astype(str)
        values = values[values.str.strip() != ""]
        if d.column in keys and not d.relabel and len(values) and values.str.contains(r"\d").mean() >= 0.2:
            continue
        # only word labels: numbers kept as text in stacked tables ('46.9', 'Rs. 277') are data, and
        # period labels ('Q3FY23') are the time axis - neither is a name to fabricate
        labels = [v for v in dict.fromkeys(values.tolist()) if is_word_label(v)]
        if labels:
            out.append(LabelColumn(f"{table_id}.{d.column}", d.column, meanings.get(d.column, ""), labels,
                                   d.risk.value, must_replace=d.relabel))
    return out


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
    source_displays: dict[str, pd.DataFrame] = {}
    kept = {normalise_text(v) for v in plan.preserved_values} | set(STRUCTURAL_LABELS)
    for table in doc.tables:
        synthetic = data.tables.get(table.table_id)
        if synthetic is None:
            continue
        table_profiles = profiles.get(table.table_id)
        source = table_frame(table, table_profiles)[0] if table_profiles else _raw_frame(table)
        table_decisions = decisions.get(table.table_id, {})

        for column, decision in table_decisions.items():
            kept_here = kept | {normalise_text(v) for v in plan.kept_labels.get(f"{table.table_id}.{column}", [])}
            if (decision.risk is SensitivityClass.DIRECT_IDENTIFIER or decision.relabel) \
                    and not decision.exempt and column in synthetic.columns and column in source.columns:
                leaked = {v for v in leaked_values(synthetic[column], source[column])
                          if normalise_text(v) not in kept_here}
                if leaked:
                    violations.append(Violation(
                        check="identifier_reuse", severity=Severity.ERROR, table_id=table.table_id,
                        column=column,
                        message=f"{len(leaked)} generated values reuse a source identifier",
                        repairable=True))
            replaced = {normalise_text(k) for k in plan.label_maps.get(f"{table.table_id}.{column}", {})}
            if replaced and not decision.relabel and column in synthetic.columns:
                survived = {normalise_text(v) for v in synthetic[column].dropna().astype(str)} & replaced
                if survived:                           # E-35: a label that had to be fabricated is still there
                    violations.append(Violation(
                        check="label_reuse", severity=Severity.ERROR, table_id=table.table_id, column=column,
                        message=f"{len(survived)} source labels that had to be replaced are still shown",
                        repairable=True))
            if (not decision.exempt and column in synthetic.columns and column in source.columns
                    and not pd.api.types.is_numeric_dtype(source[column])):
                same = [i for i, (a, b) in enumerate(zip(source[column].astype(str), synthetic[column].astype(str)))
                        if a == b and parse_number(a) is not None and parse_period(a) is None
                        and a.strip() not in STRUCTURAL_NUMBERS]
                if same:                               # a number kept as text, printed as in the source
                    violations.append(Violation(
                        check="text_number_reuse", severity=Severity.ERROR, table_id=table.table_id,
                        column=column, rows=same[:20],
                        message=f"{len(same)} numbers written as text are unchanged from the source",
                        repairable=True))

        identifiers = set(data.key_columns.get(table.table_id, []))   # IDs are labels, not measurements
        numeric = [c for c, d in table_decisions.items()
                   if c in source.columns and c in synthetic.columns and c not in identifiers
                   and pd.api.types.is_numeric_dtype(source[c]) and not d.exempt
                   and d.risk not in (SensitivityClass.DIRECT_IDENTIFIER, SensitivityClass.DERIVED)]
        key = next(iter(data.key_columns.get(table.table_id, [])), None)
        computed = _computed_rows(data.rules, table.table_id, source)    # e.g. Gross profit rows
        found = value_distance_violations(table.table_id, source, synthetic, key, numeric,
                                          th.min_value_distance)
        shown = data.display.get(table.table_id)
        source_shown = _as_displayed(source, table_profiles or [])     # the source in display units
        source_displays[table.table_id] = source_shown
        if shown is not None:                         # rounding for print must not undo the distance
            found += [v.model_copy(update={"check": "value_distance_displayed",
                                           "message": v.message + " after rounding for display"})
                      for v in value_distance_violations(table.table_id, source_shown, shown, key, numeric,
                                                         th.min_value_distance)]
        for v in found:
            rows = [r for r in v.rows if r not in computed]
            if rows:
                violations.append(v.model_copy(update={"rows": rows}))

        quasi = [c for c, d in table_decisions.items()
                 if d.risk is SensitivityClass.QUASI_IDENTIFIER
                 and c in source.columns and c in synthetic.columns]
        violations += quasi_row_matches(table.table_id, source, synthetic, quasi)
        violations += rare_combination_violations(table.table_id, synthetic, quasi, th)

    # Headline Distance: the SAME fact definitions on both sides (core.facts), level facts only -
    # growth and shares are ratios, which a scaled statement keeps on purpose
    source_facts = _headline(compute_facts(SimpleNamespace(display=source_displays,
                                                           source_profiles=data.source_profiles,
                                                           key_columns=data.key_columns)))
    synthetic_facts = _headline(data.facts)
    violations += headline_violations(source_facts, synthetic_facts, th.min_headline_distance)

    return PrivacyReport(ok=not any(v.severity is Severity.ERROR for v in violations),
                         checks_run=list(CHECKS_RUN), violations=violations)
