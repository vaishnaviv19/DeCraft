# src/inference/__init__.py  -- PUBLIC INTERFACE of the inference module (deep module)
"""Grade, profile and analyse every Source Table; propose (LLM) and verify (Python) rules; detect
relationships, patterns and text facts.

M2-M3: the Annotator proposes, Python verifies, `classify_rule` is the single policy that decides
tier, status and Enforcement, and Primary/Foreign Keys plus Aggregate Rules are inferred. Patterns
follow in M6.
"""
from __future__ import annotations


import pandas as pd

from src.config import Config
from src.contracts import (Evidence, ExtractionGrade, InferenceReport, Rule, RuleKind, SourceDocument)
from src.core.aggregation import AggregateRule, detect_aggregate_rules
from src.core.frames import table_frame
from src.core.periods import fiscal_year_end_month, parse_period
from src.core.rows import statement_label_column
from src.errors import FormulaSyntaxError
from src.inference._annotator import Proposal, TablePayload, annotate_table
from src.inference._formula import parse_formula
from src.inference._profile import profile_table
from src.inference._quality import downgrade_for_total_mismatch, grade_table
from src.inference._refdate import infer_reference_date
from src.inference._linker import link_tables
from src.inference._patterns import detect_patterns, explain_breaks
from src.inference._relationships import best_per_child, candidate_keys, detect_relationships
from src.inference._totals import additive_columns, detect_total_columns, total_row_links
from src.inference._verifiers import (VerifyResult, classify_rule, proposal_formula_text, row_formula_spec,
                                      verify_proposal)
from src.llm import LLMGateway

__all__ = ["infer", "statement_labels"]

KIND_MAP = {"formula": RuleKind.FORMULA, "derived_from": RuleKind.DATE_DERIVATION,
            "determines": RuleKind.DETERMINES, "ordering": RuleKind.ORDERING,
            "hierarchy": RuleKind.HIERARCHY, "conditional": RuleKind.CONDITIONAL,
            "row_formula": RuleKind.ROW_FORMULA}


def statement_labels(df, profiles) -> tuple[str | None, list[str]]:
    """A statement-like table -> its label column and ALL row labels, so the LLM can see relations such
    as Gross profit = Revenue - Cost of sales. Other tables (customer lists ...) send nothing extra."""
    label = statement_label_column(df, next((p.column for p in profiles if p.physical_type == "text"), None))
    if label is None:
        return None, []
    return label, [str(v).strip() for v in df[label].dropna() if str(v).strip()]


def infer(doc: SourceDocument, cfg: Config, gateway: LLMGateway | None = None) -> InferenceReport:
    """Grade, profile and analyse every table; never raises for bad or ambiguous data: it records
    warnings and lowers tiers instead."""
    th = cfg.thresholds
    report = InferenceReport(source_sha256=doc.document_sha256, config_hash=cfg.config_hash(),
                             llm_used=False)

    doc_text = " ".join(doc.text_blocks)
    periods = [p for t in doc.tables for row in t.raw_cells for v in row
               if not str(v).strip().upper().endswith(("E", "EST"))
               and (p := parse_period(str(v))) is not None]
    report.reference_date = infer_reference_date(doc_text, periods, fiscal_year_end_month(doc_text))
    frames: dict[str, pd.DataFrame] = {}
    payloads: dict[str, TablePayload] = {}
    llm_on = cfg.llm_enabled and gateway is not None and gateway.enabled

    for table in doc.tables:
        quality = grade_table(table)
        report.table_quality[table.table_id] = quality
        if quality.grade is ExtractionGrade.BAD:
            report.warnings.append(
                f"{table.table_id}: skipped (bad extraction: {'; '.join(quality.issues)})")
            continue

        profiles, warnings = profile_table(table, quality, cfg)
        report.warnings.extend(warnings)
        df, decimals = table_frame(table, profiles)

        # Total Rows and Additive columns (M2.5); totals that do not reconcile downgrade the table.
        label_col = next((p.column for p in profiles if p.physical_type == "text"), None)
        links = total_row_links(df, label_col)
        additive = additive_columns(df, links, th.formula_strong_match)
        testable = [v for v in additive.values() if v is not None]
        quality = downgrade_for_total_mismatch(
            quality, (sum(testable) / len(testable)) if testable else None, th.formula_strong_match)
        report.table_quality[table.table_id] = quality
        report.profiles.extend(
            p.model_copy(update={"additive": additive.get(p.column), "extraction_grade": quality.grade})
            for p in profiles)

        if len(set(table.header)) != len(table.header):
            report.warnings.append(f"{table.table_id}: duplicate column names -> rules skipped")
            continue
        frames[table.table_id] = df
        report.key_columns[table.table_id] = candidate_keys(df)
        report.patterns.extend(detect_patterns(table.table_id, df, profiles, th, cfg.seed))

        proposals: list[tuple[str, Proposal]] = []
        label_column, row_labels = statement_labels(df, profiles)
        payloads[table.table_id] = TablePayload(
            name=table.table_id, caption=table.caption, columns=list(df.columns),
            sample_rows=[list(r) for r in table.raw_cells[:3]], context_text=table.context_text,
            label_column=label_column, row_labels=row_labels)
        if llm_on:
            annotation = annotate_table(payloads[table.table_id], gateway)
            if annotation is not None:
                report.llm_used = True
                if report.domain == "unknown":
                    report.domain = annotation.domain
                    report.domain_confidence = annotation.domain_confidence
                proposals.extend(("llm_proposal", p) for p in annotation.proposals)
                for meaning in annotation.columns:
                    key = f"{table.table_id}.{meaning.column}"
                    report.sensitivity_labels[key] = meaning.sensitivity
                    report.column_importance[key] = meaning.importance
                    report.column_meanings[key] = meaning.meaning
                    report.column_roles[key] = meaning.role
                    report.sensitivity_confidence[key] = meaning.confidence
                    if meaning.sensitivity_reason:
                        report.sensitivity_reasons[key] = meaning.sensitivity_reason

        proposals.extend(
            ("rule_detector", Proposal(kind="formula", columns=[],
                                       params={"expression": text}, rationale="total column"))
            for text in detect_total_columns(df))

        for n, (source, p) in enumerate(proposals, start=1):
            kind = KIND_MAP.get(p.kind)
            if kind is None:
                continue
            result = verify_proposal(df, p, report.reference_date, decimals, th)
            tier, status, enforcement, reason = classify_rule(
                result, th, p.stated_in_text, quality.grade)
            formula = None
            if p.kind == "formula":
                try:
                    formula = parse_formula(proposal_formula_text(p), list(df.columns))
                except FormulaSyntaxError:
                    formula = None
            elif p.kind == "row_formula":
                try:
                    formula, _ = row_formula_spec(df, p.params.get("label_column") or p.columns[0],
                                                  p.params.get("expression", ""))
                except (FormulaSyntaxError, IndexError):
                    formula = None
            if p.kind == "row_formula":
                columns = list(p.columns)                  # the label column; the rows are in `formula`
            else:
                columns = [formula.output, *formula.inputs] if formula else list(p.columns)
            evidence = [Evidence(source=source, detail=p.rationale or p.kind),
                        Evidence(source="stat_test",
                                 detail=f"{result.detail}: match_rate={result.match_rate} "
                                        f"on {result.n_rows} rows")]
            if p.stated_in_text:
                evidence.append(Evidence(source="context_text", detail="rule stated in the PDF text"))
            report.rules.append(Rule(
                rule_id=f"{table.table_id}.r{n}", kind=kind, table_id=table.table_id, columns=columns,
                formula=formula, params=dict(p.params), tier=tier, status=status,
                enforcement=enforcement, match_rate=result.match_rate, n_rows=result.n_rows,
                rel_tol=th.rel_tol_default, extraction_grade=quality.grade, evidence=evidence,
                reason=reason))

    # Cross-table links and totals. The key-based link search is cheap and always runs. With the LLM
    # on, the Cross-table Linker proposes links and totals and Python tests only those; the
    # try-every-combination total search runs only when the LLM is off or gave no valid answer.
    grades = {t: report.table_quality[t].grade for t in frames}
    relationships = detect_relationships(frames, th, grades)
    aggregates: list[tuple[AggregateRule, str, str]] | None = None
    if llm_on and len(frames) >= 2:
        linked = link_tables([payloads[t] for t in frames], frames, gateway, th, grades)
        if linked is not None:
            report.llm_used = True
            report.warnings.extend(linked.warnings)
            relationships = best_per_child(linked.relationships, relationships)
            aggregates = [(agg, "llm_proposal", why) for agg, why in linked.aggregates]
    if aggregates is None:
        aggregates = [(agg, "rule_detector", f"{agg.agg} of {agg.detail}.{agg.detail_col}")
                      for agg in detect_aggregate_rules(frames)]
    report.relationships = relationships
    if llm_on:                                     # confirmed breaks: the cause, if the text states it
        explain_breaks(report.patterns, frames,
                       " ".join([doc_text] + [t.context_text for t in doc.tables if t.context_text]), gateway)
    for n, (agg, source, why) in enumerate(aggregates, start=1):
        grade = report.table_quality[agg.summary].grade
        tier, status, enforcement, reason = classify_rule(
            VerifyResult(agg.match_rate, agg.n_rows, f"aggregate {agg.agg}"), th, False, grade)
        report.rules.append(Rule(
            rule_id=f"{agg.summary}.agg{n}", kind=RuleKind.AGGREGATE, table_id=agg.summary,
            columns=[agg.summary_col],
            params={"detail": agg.detail, "detail_col": agg.detail_col,
                    "summary_col": agg.summary_col, "group_col": agg.group_col,
                    "label_col": agg.label_col, "agg": agg.agg},
            tier=tier, status=status, enforcement=enforcement, match_rate=agg.match_rate,
            n_rows=agg.n_rows, rel_tol=th.rel_tol_default, extraction_grade=grade,
            evidence=[Evidence(source=source, detail=why),
                      Evidence(source="stat_test",
                               detail=f"match_rate={agg.match_rate} on {agg.n_rows} labels")],
            reason=reason))
    return report
