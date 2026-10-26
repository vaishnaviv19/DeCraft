# src/generation/_prepare.py
"""Data preparation for the report (M8.4): period shift, fabricated labels, row counts, recovered
tables, display tables and facts with bands/owners. `generate()` stays unchanged."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from src.config import Config
from src.contracts import (ChartSpec, Enforcement, SourceDocument, SourceTable, RuleKind)
from src.contracts.dataset import CanonicalDataset
from src.core.facts import compute_facts
from src.core.hierarchy import total_row_links
from src.core.periods import format_like, parse_period, shift_period
from src.errors import HardConstraintError
from src.llm import LLMGateway
from src.core.originality import name_words, source_proper_nouns
from src.privacy import (LabelColumn, document_values, fabricate_labels, is_word_label,
                         normalise_text)
from src.validation import validate






def prepare_for_report(data: CanonicalDataset, report, figures, recovered, templates, cfg: Config,
                       rng, gateway: LLMGateway | None = None,
                       doc: SourceDocument | None = None, privacy_plan=None,
                       out_dir: Path | None = None) -> CanonicalDataset:
    """Period shift (E-16), fabricated labels (E-27/E-28; decided by the privacy plan's Label Fabricator, E-35/E-39), row counts (E-17), recovered tables
    (E-19), display tables and facts with bands. Re-runs validation and raises on failure."""
    data = _shift_periods(data, report, cfg, doc)
    data = _fabricate_labels(data, report, cfg, gateway=gateway, doc=doc, rng=rng, privacy_plan=privacy_plan,
                             out_dir=out_dir)
    data = _enforce_row_counts(data, report, cfg, rng)
    data = _add_recovered(data, report, recovered, cfg, rng)
    data.display = _display_tables(data, report)
    _bands_and_owners(data, templates)
    result = validate(data, report, cfg, source=doc)
    if not result.ok:
        raise HardConstraintError(result.model_dump_json())
    data.validation = result
    return data


# ---------------------------------------------------------------- periods
def _rename_expr(expr, rename: dict[str, str]) -> None:
    if expr.op == "col" and expr.col in rename:
        expr.col = rename[expr.col]
    for argument in expr.args:
        _rename_expr(argument, rename)


def _shift_periods(data: CanonicalDataset, report, cfg: Config,
                   doc: SourceDocument | None = None) -> CanonicalDataset:
    years = cfg.report.period_shift_years
    if not years:
        return data
    renames: dict[str, dict[str, str]] = {}
    labels: set[str] = set()

    for table_id, frame in data.tables.items():
        rename: dict[str, str] = {}
        for column in list(frame.columns):
            parsed = parse_period(str(column))
            if parsed is not None and str(column).strip() and not str(column).isdigit():
                new = format_like(str(column), shift_period(parsed, years))
                if new != str(column):
                    rename[str(column)] = new
                    labels.update({str(column), new})
        for profile in [p for p in report.profiles if p.table_id == table_id]:
            if profile.physical_type == "period" and profile.column in frame.columns:
                def shifted(value):
                    parsed = parse_period(str(value)) if value is not None and str(value).strip() else None
                    if parsed is None:
                        return value
                    new = format_like(str(value).strip(), shift_period(parsed, years))
                    labels.update({str(value), new})
                    return new
                frame[profile.column] = frame[profile.column].map(shifted)
            profile.column = rename.get(profile.column, profile.column)
        if rename:
            frame.rename(columns=rename, inplace=True)
            renames[table_id] = rename
        if doc is not None:
            source_table = next((t for t in doc.tables if t.table_id == table_id), None)
            if source_table is not None:
                source_table.header = [rename.get(str(header), str(header))
                                       for header in source_table.header]
                for profile in [p for p in report.profiles if p.table_id == table_id
                                and p.physical_type == "period"]:
                    position = source_table.header.index(profile.column) \
                        if profile.column in source_table.header else None
                    if position is None:
                        continue
                    for row in source_table.raw_cells:
                        if position < len(row):
                            value = row[position]
                            parsed = parse_period(str(value)) if value is not None else None
                            if parsed is not None:
                                row[position] = format_like(str(value).strip(),
                                                            shift_period(parsed, years))

    for rule in report.rules:
        rename = renames.get(rule.table_id, {})
        if not rename:
            continue
        rule.columns = [rename.get(column, column) for column in rule.columns]
        if rule.formula is not None:
            rule.formula.output = rename.get(rule.formula.output, rule.formula.output)
            _rename_expr(rule.formula.expr, rename)
        rule.params = {key: rename.get(value, value) if isinstance(value, str) else value
                       for key, value in rule.params.items()}

    data.lineage = {_rename_node(node, renames): method for node, method in data.lineage.items()}
    data.key_columns = {table: [renames.get(table, {}).get(c, c) for c in columns]
                        for table, columns in data.key_columns.items()}
    allowed = set(labels)
    for label in list(labels):
        match = re.fullmatch(r"FY(\d{2,4})[A-Z]?", label.strip(), re.I)
        if match:
            year = match.group(1)
            allowed.add(f"FY20{year}" if len(year) == 2 else f"FY{year}")
            allowed.add(f"FY{int(year) % 100:02d}" if len(year) == 4 else f"FY{year}")
    data.allowed_periods = sorted(allowed)
    return data


def _rename_node(node: str, renames: dict[str, dict[str, str]]) -> str:
    table, _, column = node.partition(".")
    return f"{table}.{renames.get(table, {}).get(column, column)}"


# ---------------------------------------------------------------- labels


def _fabricate_labels(data: CanonicalDataset, report, cfg: Config,
                      gateway: LLMGateway | None, doc: SourceDocument | None,
                      rng, privacy_plan=None, out_dir: Path | None = None) -> CanonicalDataset:
    """E-35 / E-39: ONE Label Fabricator decides labels, in the privacy plan. Here only labels that
    nobody has decided yet (e.g. hidden recovered-chart tables) and that are still source text go
    through that same fabricator. Labels the plan kept as standard vocabulary stay, fakes stay fakes,
    the organisation name and address stay."""
    kept, made, preserved, avoid = set(), set(), [], set()
    if privacy_plan is not None:
        kept = {normalise_text(v) for values in privacy_plan.kept_labels.values() for v in values}
        made = {normalise_text(v) for mapping in privacy_plan.label_maps.values() for v in mapping.values()}
        preserved = list(privacy_plan.preserved_values)
        avoid = set(privacy_plan.avoid)
    text = " ".join(doc.text_blocks) if doc is not None else ""
    entity = doc.entity_name if doc is not None else None
    if not avoid:
        avoid = source_proper_nouns(text, keep=set(preserved) | ({entity} if entity else set()))
    if out_dir is not None:                    # the stage runner's folder; the library writes nowhere else
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "avoid.json").write_text(json.dumps(sorted(avoid), indent=2), encoding="utf-8")

    source_values = document_values(doc) if doc is not None else set()
    skip = kept | made | {normalise_text(p) for p in preserved}
    columns = []
    for table_id, frame in data.tables.items():
        profiles = {p.column: p for p in report.profiles if p.table_id == table_id}
        for column in frame.columns:
            profile = profiles.get(str(column))
            if profile is None or profile.physical_type != "text" or profile.semantic_type == "identifier":
                continue
            labels = [v for v in dict.fromkeys(frame[column].dropna().astype(str).str.strip())
                      if is_word_label(v) and normalise_text(v) not in skip
                      and (not source_values or normalise_text(v) in source_values)]
            if labels:
                columns.append(LabelColumn(f"{table_id}.{column}", str(column), "", labels))
    mapping: dict[str, str] = {}
    if columns:
        result = fabricate_labels(columns, gateway if gateway is not None and gateway.enabled else None,
                                  source_values, avoid, name_words(text, entity), preserved,
                                  getattr(privacy_plan, "locale", None), rng)
        for column_map in result.maps.values():
            mapping.update(column_map)
        for frame in list(data.tables.values()) + list(data.display.values()):
            for column in frame.columns:
                # pandas 3 gives text columns the 'str' dtype, not object: test for "not numbers/dates"
                if not (pd.api.types.is_numeric_dtype(frame[column])
                        or pd.api.types.is_datetime64_any_dtype(frame[column])):
                    frame[column] = frame[column].map(
                        lambda value: mapping.get(str(value).strip(), value) if isinstance(value, str) else value)
    if out_dir is not None:
        (Path(out_dir) / "labels.json").write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    return data


# ---------------------------------------------------------------- row counts
def _enforce_row_counts(data: CanonicalDataset, report, cfg: Config, rng) -> CanonicalDataset:
    tolerance = cfg.report.row_tolerance
    for table_id, frame in list(data.tables.items()):
        profiles = [p for p in report.profiles if p.table_id == table_id]
        if not profiles:
            continue
        source_rows = max(profile.n for profile in profiles)
        label = next((p.column for p in profiles if p.physical_type == "text"), None)
        links = total_row_links(frame, label)
        leaves = [i for i in range(len(frame)) if i not in links]
        low, high = max(1, round(source_rows * (1 - tolerance))), max(1, round(source_rows * (1 + tolerance)))
        if low <= len(leaves) <= high:
            continue
        target = int(rng.integers(low, high + 1))
        sampled = frame.loc[leaves].sample(n=target, replace=target > len(leaves),
                                           random_state=int(rng.integers(1 << 31)))
        new = pd.concat([sampled, frame.loc[list(links)]] if links else [sampled], ignore_index=True)
        for rule in report.rules:
            if rule.table_id == table_id and rule.kind is RuleKind.FORMULA \
                    and rule.enforcement is Enforcement.HARD and rule.formula is not None:
                from src.core.expr import evaluate
                new[rule.formula.output] = evaluate(rule.formula.expr, new)
        new_links = total_row_links(new, label)
        for profile in profiles:
            if not profile.additive or profile.column not in new.columns or not new_links:
                continue
            from src.core.rollup import rollup as rollup_values
            leaf_values = {i: float(new.at[i, profile.column]) for i in range(len(new))
                           if i not in new_links and pd.notna(new.at[i, profile.column])}
            for total, value in rollup_values(new_links, leaf_values).items():
                if total not in leaf_values:
                    new.at[total, profile.column] = value
        data.tables[table_id] = new
    return data


# ---------------------------------------------------------------- recovered charts
def _add_recovered(data: CanonicalDataset, report, recovered, cfg: Config,
                   rng) -> CanonicalDataset:
    if not recovered:
        return data
    from src.generation import generate
    from src.inference import infer
    from src.privacy import plan_privacy
    from src.core.recovered import recovered_to_source_table

    for chart in recovered:
        table = recovered_to_source_table(chart)
        document = SourceDocument(document_sha256=report.source_sha256, tables=[table],
                                  text_blocks=[])
        sub_report = infer(document, cfg, None)
        sub_plan = plan_privacy(document, sub_report, cfg)
        sub_data = generate(document, sub_report, sub_plan, cfg, rng)
        for table_id, frame in sub_data.tables.items():
            data.tables[table_id] = frame
            data.display[table_id] = sub_data.display[table_id]
            series = [str(c) for c in frame.columns if str(c) != "Category"]
            data.charts[f"chart_{chart.figure_id}"] = ChartSpec(
                chart_id=f"chart_{chart.figure_id}", table_id=table_id, label_column="Category",
                series_columns=series or [str(frame.columns[-1])], title=f"Recovered: {chart.figure_id}",
                display=False)
    return data


# ---------------------------------------------------------------- display and facts
def _display_tables(data: CanonicalDataset, report) -> dict[str, pd.DataFrame]:
    from src.core.rounding import build_display_df

    display: dict[str, pd.DataFrame] = {}
    for table_id, frame in data.tables.items():
        profiles = [p for p in report.profiles if p.table_id == table_id]
        decimals = {p.column: p.decimals for p in profiles if p.decimals is not None}
        label = next((p.column for p in profiles if p.physical_type == "text"), None)
        links = total_row_links(frame, label)
        additive = any(p.additive for p in profiles)
        display[table_id] = build_display_df(
            frame, decimals, links, "sum_of_displayed" if additive else "true_total")
    return display


def _growth_band(value: float) -> str:
    if value < -10:
        return "sharp decline"
    if value < -2:
        return "decline"
    if value <= 2:
        return "flat"
    if value <= 10:
        return "increase"
    return "strong increase"


def _share_band(value: float) -> str:
    if value >= 50:
        return "dominant"
    if value >= 25:
        return "major"
    return "minor"


def _bands_and_owners(data: CanonicalDataset, templates) -> None:
    facts = compute_facts(data)
    owner = "S1"
    if templates:
        for page in templates:
            heading = next((b for b in page.blocks if b.section_id), None)
            if heading is not None:
                owner = heading.section_id or "S1"
                break
    for key, fact in facts.items():
        if key.endswith(".growth") and isinstance(fact.value, (int, float)):
            fact.band = _growth_band(float(fact.value))
        elif key.endswith(".largest_share") and isinstance(fact.value, (int, float)):
            fact.band = _share_band(float(fact.value))
        fact.owner = owner
    data.facts = facts
