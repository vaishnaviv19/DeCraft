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
from src.core.periods import format_like, parse_period, shift_period, shift_text_periods
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
    _source_column_order(data, doc)
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
        # period labels sitting in CELLS of other columns (a stacked sub-header row such as
        # 'Shareholding % | Q3FY23 | Q4FY23 | Q1FY24'), shifted in their own spelling. Live run
        # 2026-10-26: the header said Q1FY25 while this row still said Q1FY24.
        period_columns = {p.column for p in report.profiles
                          if p.table_id == table_id and p.physical_type == "period"}
        for column in list(frame.columns):
            if column in period_columns or pd.api.types.is_numeric_dtype(frame[column]):   # pandas 3: text is 'str', not object
                continue

            def cell_label(value):
                text = value.strip() if isinstance(value, str) else ""
                if not text or len(text) > 24 or not re.search(r"[A-Za-z]", text)                         or parse_period(text) is None:
                    return value
                new = shift_text_periods(text, years)
                if new != text:
                    labels.update({text, new})
                return new
            frame[column] = frame[column].map(cell_label)
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
    """E-19 / E-47: a recovered source chart becomes a HIDDEN table ('rc_<figure_id>'): its categories
    are the source's axis labels with periods shifted forward (E-16), and every value is the recovered
    value times an independent Safe Factor per cell (v3.3 F-01), so nothing plotted is the real figure.
    Before, the generic generator turned the date axis into NaN and multiplied the values ~1e9 (it read
    the main document's 'Rs cr' scale); the chart printed 'nan' labels and 1e11 ticks (2026-10-26)."""
    if not recovered:
        return data
    from src.core.periods import shift_text_periods
    from src.generation._tiny import jitter_cells_by_cell

    th = cfg.thresholds
    for chart in recovered:
        table_id = f"rc_{chart.figure_id}"
        columns = {"Category": [shift_text_periods(str(c), cfg.report.period_shift_years)
                                for c in chart.categories]}
        for series in chart.series:
            values = jitter_cells_by_cell([float(v) for v in series.values], rng, th.jitter_low,
                                          th.jitter_high, th.min_value_distance, 0.0)
            columns[series.name] = [round(v, 1) for v in values]
        frame = pd.DataFrame(columns)
        data.tables[table_id] = frame
        data.display[table_id] = frame.copy()
        series_names = [s.name for s in chart.series]
        data.charts[f"chart_{chart.figure_id}"] = ChartSpec(
            chart_id=f"chart_{chart.figure_id}", table_id=table_id, label_column="Category",
            series_columns=series_names, title=" and ".join(series_names),   # never an internal id
            kind="line" if len(chart.categories) > 2 else "bar", display=False)
    return data


# ---------------------------------------------------------------- display and facts
def _source_column_order(data: CanonicalDataset, doc: SourceDocument | None) -> None:
    """Tables keep the SOURCE's column order (E-17: same columns as the source). Generation puts the
    label column last; the rendered tables printed 'FY22A ... | Y.E March' with the row labels on the
    right (live run 2026-10-26). Unknown columns keep their place after the source ones."""
    if doc is None:
        return
    headers = {table.table_id: [str(h) for h in table.header] for table in doc.tables}
    for frames in (data.tables, data.display):
        for table_id, frame in list(frames.items()):
            order = [h for h in headers.get(table_id, []) if h in frame.columns]
            if not order:
                continue
            frames[table_id] = frame[order + [c for c in frame.columns if c not in order]]


def _display_tables(data: CanonicalDataset, report) -> dict[str, pd.DataFrame]:
    from src.core.rounding import build_display_df

    display: dict[str, pd.DataFrame] = {}
    for table_id, frame in data.tables.items():
        profiles = [p for p in report.profiles if p.table_id == table_id]
        decimals = {p.column: p.decimals for p in profiles if p.decimals is not None}
        label = next((p.column for p in profiles if p.physical_type == "text"), None)
        links = total_row_links(frame, label)
        additive = any(p.additive for p in profiles)
        # display in SOURCE units: a 'Rs cr' column is divided back by its scale (live run 2026-10-26:
        # facts read 876,271,442,312 instead of 87,627.1 and the prose printed base-unit figures)
        scales = {p.column: p.scale for p in profiles if p.scale not in (None, 0, 1)}
        display[table_id] = build_display_df(
            frame, decimals, links, "sum_of_displayed" if additive else "true_total", scales=scales)
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
