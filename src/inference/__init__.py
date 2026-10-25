# src/inference/__init__.py  -- PUBLIC INTERFACE of the inference module (deep module)
"""Grade, profile and analyse every Source Table; propose (LLM) and verify (Python) rules; detect
relationships, patterns and text facts.

M2-M3: the Annotator proposes, Python verifies, `classify_rule` is the single policy that decides
tier, status and Enforcement, and Primary/Foreign Keys plus Aggregate Rules are inferred. Patterns
follow in M6.
"""
from __future__ import annotations

from src.config import Config
from src.contracts import (Evidence, ExtractionGrade, InferenceReport, Rule, RuleKind, SourceDocument)
from src.core.aggregation import detect_aggregate_rules
from src.core.frames import table_frame
from src.core.periods import fiscal_year_end_month, parse_period
from src.errors import FormulaSyntaxError
from src.inference._annotator import Proposal, TablePayload, annotate_table
from src.inference._formula import parse_formula
from src.inference._profile import profile_table
from src.inference._quality import downgrade_for_total_mismatch, grade_table
from src.inference._refdate import infer_reference_date
from src.inference._relationships import detect_relationships
from src.inference._totals import additive_columns, detect_total_columns, total_row_links
from src.inference._verifiers import (VerifyResult, classify_rule, proposal_formula_text,
                                      verify_proposal)
from src.llm import LLMGateway

__all__ = ["infer"]

KIND_MAP = {"formula": RuleKind.FORMULA, "derived_from": RuleKind.DATE_DERIVATION,
            "determines": RuleKind.DETERMINES, "ordering": RuleKind.ORDERING,
            "hierarchy": RuleKind.HIERARCHY, "conditional": RuleKind.CONDITIONAL}


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

        proposals: list[tuple[str, Proposal]] = []
        if cfg.llm_enabled and gateway is not None and gateway.enabled:
            annotation = annotate_table(TablePayload(
                name=table.table_id, caption=table.caption, columns=list(df.columns),
                sample_rows=[list(r) for r in table.raw_cells[:3]], context_text=table.context_text),
                gateway)
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

    report.relationships = detect_relationships(
        frames, th, {t: report.table_quality[t].grade for t in frames})
    for n, agg in enumerate(detect_aggregate_rules(frames), start=1):
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
            evidence=[Evidence(source="rule_detector",
                               detail=f"{agg.agg} of {agg.detail}.{agg.detail_col}"),
                      Evidence(source="stat_test",
                               detail=f"match_rate={agg.match_rate} on {agg.n_rows} labels")],
            reason=reason))
    return report
