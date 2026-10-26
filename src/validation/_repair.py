# src/validation/_repair.py
"""Bounded repair (fixes B-01). Every repair is followed by full validation; the verdict comes from the
validation after the last repair. Repair strategies never weaken a Rule: they only apply it."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, TypeVar

import numpy as np
import pandas as pd

from src.contracts import Enforcement, RepairRecord, RuleKind, ValidationReport
from src.contracts.dataset import CanonicalDataset
from src.core.aggregation import AggregateRule, apply_aggregate_rule
from src.core.expr import evaluate
from src.core.rows import apply_row_formulas
from src.core.hierarchy import total_row_links
from src.core.rollup import rollup as rollup_values
from src.errors import HardConstraintError
from src.core.distance import ensure_distance
from src.core.keys import fresh_key_values

D = TypeVar("D")


def run_bounded_repair(data: D, validate: Callable[[D], ValidationReport],
                       repair: Callable[[D, ValidationReport, int], tuple[D, str, int]],
                       max_repairs: int) -> tuple[D, ValidationReport]:
    """validate(data) -> report; repair(data, report, attempt) -> (new_data, action, rows_affected)."""
    report = validate(data)
    history: list[RepairRecord] = []
    attempt = 0
    while not report.ok and attempt < max_repairs:
        if not report.repairable:
            break
        attempt += 1
        data, action, rows = repair(data, report, attempt)
        report = validate(data)
        history.append(RepairRecord(attempt=attempt, action=action,
                                    rule_ids=sorted({v.rule_id for v in report.violations if v.rule_id}),
                                    rows_affected=rows, ok_after=report.ok))
    report = report.model_copy(update={"repairs": history})
    if not report.ok:
        raise HardConstraintError(report.model_dump_json())
    return data, report


def _pattern(inference, pattern_id):
    return next((p for p in getattr(inference, "patterns", []) if p.pattern_id == pattern_id), None)


def repair_dataset(data: CanonicalDataset, report, cfg, inference=None, rng=None,
                   frames: dict[str, pd.DataFrame] | None = None
                   ) -> tuple[CanonicalDataset, str, int]:
    """Deterministic repair order: numbers -> ranges -> keys -> foreign keys -> derived -> totals ->
    aggregates -> value distance. Each step only applies a Rule or a stated constraint."""
    if rng is None:
        rng = np.random.default_rng(cfg.seed)
    tables = {table_id: frame.copy(deep=True) for table_id, frame in data.tables.items()}
    actions: list[str] = []
    touched = 0

    for violation in report.violations:
        frame = tables.get(violation.table_id) if violation.table_id else None
        column = violation.column
        if violation.check == "invalid_number" and frame is not None and column in frame.columns:
            values = pd.to_numeric(frame[column], errors="coerce")
            fill = float(values.median()) if values.notna().any() else 0.0
            for i in violation.rows:                          # only the flagged cells: real blanks stay
                frame.iloc[i, frame.columns.get_loc(column)] = fill
            touched += len(violation.rows)
            actions.append(f"filled {len(violation.rows)} invalid numbers in {column}")
        elif violation.check == "negative_value" and frame is not None and column in frame.columns:
            for i in violation.rows:
                frame.iloc[i, frame.columns.get_loc(column)] = 0.0
            touched += len(violation.rows)
            actions.append(f"clipped {len(violation.rows)} negative values in {column}")
        elif violation.check == "primary_key" and frame is not None and column in frame.columns:
            # replace only the missing / repeated keys (the first copy of a value stays, so links to it
            # remain valid); new keys follow the column's format and are above every existing key
            keys = frame[column]
            broken = [i for i in range(len(keys)) if pd.isna(keys.iloc[i]) or keys.iloc[:i].eq(keys.iloc[i]).any()]
            if broken:
                new = fresh_key_values(keys, len(broken), rng)
                for i, value in zip(broken, new):
                    frame.iloc[i, frame.columns.get_loc(column)] = value
            touched += len(broken)
            actions.append(f"replaced {len(broken)} broken keys in {column}")
        elif violation.check == "ordering" and frame is not None and inference is not None:
            rule = next((r for r in inference.rules if r.rule_id == violation.rule_id), None)
            if rule is not None and len(rule.columns) >= 2 and all(c in frame.columns for c in rule.columns[:2]):
                earlier, later = rule.columns[0], rule.columns[1]
                gap = pd.to_timedelta(rule.params.get("min_gap_days", 0) or 0, unit="D")
                first = pd.to_datetime(frame[earlier], errors="coerce")
                for i in violation.rows:
                    frame.iloc[i, frame.columns.get_loc(later)] = first.iloc[i] + gap
                touched += len(violation.rows)
                actions.append(f"moved {len(violation.rows)} '{later}' dates after '{earlier}'")
        elif violation.check == "utility_direction" and frame is not None and frames \
                and violation.table_id in frames and inference is not None and violation.rows \
                and _pattern(inference, violation.rule_id) is not None \
                and _pattern(inference, violation.rule_id).row_label:
            # a statement line item's trend flipped: put its generated values (across the period
            # columns) in the source row's order
            pattern = _pattern(inference, violation.rule_id)
            row = violation.rows[0]
            columns = [c for c in pattern.period_columns if c in frame.columns and c in frames[violation.table_id].columns]
            source_row = pd.to_numeric(frames[violation.table_id].loc[:, columns].iloc[row], errors="coerce").to_numpy()
            generated = pd.to_numeric(frame.loc[:, columns].iloc[row], errors="coerce").to_numpy()
            ok = np.flatnonzero(np.isfinite(source_row) & np.isfinite(generated))
            ordered = np.sort(generated[ok])[np.argsort(np.argsort(source_row[ok]))]
            for k, value in zip(ok, ordered):
                frame.iloc[row, frame.columns.get_loc(columns[k])] = float(value)
            touched += len(ok)
            actions.append(f"re-ordered row {pattern.row_label} to keep its trend")
        elif violation.check == "utility_direction" and frame is not None and frames \
                and column in frame.columns and violation.table_id in frames:
            # a confirmed trend flipped: keep the generated values but put them in the source's order
            source = pd.to_numeric(frames[violation.table_id][column], errors="coerce")
            values = pd.to_numeric(frame[column], errors="coerce")
            ok = source.notna().to_numpy() & values.notna().to_numpy()
            idx = np.flatnonzero(ok)
            ordered = np.sort(values.to_numpy()[idx])[np.argsort(np.argsort(source.to_numpy()[idx]))]
            for i, v in zip(idx, ordered):
                frame.iloc[i, frame.columns.get_loc(column)] = float(v)
            touched += len(idx)
            actions.append(f"re-ordered {column} to keep its trend")
        elif violation.check == "foreign_key" and frame is not None and column in frame.columns:
            if inference is not None:
                rel = next((r for r in inference.relationships
                            if r.child.table_id == violation.table_id and r.child.column == column), None)
                parent = tables.get(rel.parent.table_id) if rel else None
                if parent is not None and rel.parent.column in parent.columns:
                    allowed = parent[rel.parent.column].dropna().to_numpy()
                    if len(allowed):
                        frame.loc[violation.rows, column] = rng.choice(allowed, size=len(violation.rows))
                        touched += len(violation.rows)
                        actions.append(f"re-linked {len(violation.rows)} foreign keys in {column}")

    if frames:
        for violation in report.violations:            # v3.3 F-02: a near-duplicate row moves as a whole
            if violation.check != "dcr" or not violation.table_id:
                continue
            frame, source = tables.get(violation.table_id), frames.get(violation.table_id)
            if frame is None or source is None:
                continue
            derived = {r.formula.output for r in (inference.rules if inference else [])
                       if r.table_id == violation.table_id and r.formula is not None}
            columns = [c for c in source.columns if c in frame.columns and c not in derived
                       and pd.api.types.is_numeric_dtype(source[c])]
            for i in violation.rows:
                if i >= len(frame):
                    continue
                for column in columns:
                    value = pd.to_numeric(frame[column], errors="coerce").iloc[i]
                    if pd.isna(value) or value == 0:
                        continue
                    step = float(rng.uniform(0.10, 0.25)) * (1.0 if rng.random() < 0.5 else -1.0)
                    frame.iloc[i, frame.columns.get_loc(column)] = float(value) * (1 + step)
                touched += 1
            actions.append(f"moved {len(violation.rows)} near-duplicate rows (DCR) away from the source")
        for violation in report.violations:
            if violation.check != "value_distance" or not violation.table_id:
                continue
            frame, source = tables.get(violation.table_id), frames.get(violation.table_id)
            if frame is None or source is None or violation.column not in frame.columns \
                    or violation.column not in source.columns:
                continue
            original = pd.to_numeric(source[violation.column], errors="coerce")
            places = next((p.decimals for p in (inference.profiles if inference else [])
                           if p.table_id == violation.table_id and p.column == violation.column), None)
            min_d = cfg.thresholds.min_value_distance
            moved = 0
            for i in violation.rows:
                if i >= len(frame) or i >= len(original) or pd.isna(original.iloc[i]) \
                        or original.iloc[i] == 0:
                    continue
                old = float(original.iloc[i])
                current = pd.to_numeric(frame[violation.column], errors="coerce").iloc[i]
                direction = 1.0 if (pd.isna(current) or current >= old) else -1.0
                # a RANDOM step of 5-15% (a fixed factor would let anyone divide it back out), kept
                # outside the distance after rounding for display
                step = float(rng.uniform(min_d * 1.2, min_d * 3.0))
                new = ensure_distance(old * (1 + direction * step), old, min_d, places)
                frame.iloc[i, frame.columns.get_loc(violation.column)] = (
                    round(new, places) if places is not None else new)
                moved += 1
            touched += moved
            actions.append(f"moved {moved} values away from the source")

    if inference is not None:
        for rule in inference.rules:
            if rule.enforcement is not Enforcement.HARD or rule.kind is not RuleKind.FORMULA \
                    or rule.formula is None:
                continue
            frame = tables.get(rule.table_id)
            if frame is not None and rule.formula.output in frame.columns:
                frame[rule.formula.output] = evaluate(rule.formula.expr, frame)
        row_rules = [r for r in inference.rules if r.enforcement is Enforcement.HARD
                     and r.kind is RuleKind.ROW_FORMULA and r.formula is not None]
        for table_id in sorted({r.table_id for r in row_rules}):
            source_frame = (frames or {}).get(table_id)
            if tables.get(table_id) is not None and source_frame is not None:
                decimals = {p.column: p.decimals for p in inference.profiles
                            if p.table_id == table_id and p.decimals is not None}
                apply_row_formulas(tables[table_id], source_frame,
                                   [r for r in row_rules if r.table_id == table_id], decimals)
        actions.append("recomputed derived columns")

        for table_id, frame in tables.items():
            profiles = [p for p in inference.profiles if p.table_id == table_id]
            label = next((p.column for p in profiles if p.physical_type == "text"), None)
            source_frame = frames.get(table_id) if frames else None
            links = total_row_links(source_frame if source_frame is not None else frame, label)
            if not links:
                continue
            for profile in profiles:
                if not profile.additive or profile.column not in frame.columns:
                    continue
                leaves = {i: float(frame.at[i, profile.column]) for i in range(len(frame))
                          if i not in links and pd.notna(frame.at[i, profile.column])}
                for total, value in rollup_values(links, leaves).items():
                    if total not in leaves:
                        frame.at[total, profile.column] = value

        for rule in inference.rules:
            if rule.enforcement is not Enforcement.HARD or rule.kind is not RuleKind.AGGREGATE:
                continue
            detail = tables.get(rule.params["detail"])
            summary = tables.get(rule.table_id)
            if detail is None or summary is None:
                continue
            agg = AggregateRule(detail=rule.params["detail"], summary=rule.table_id,
                                group_col=rule.params["group_col"], label_col=rule.params["label_col"],
                                detail_col=rule.params["detail_col"],
                                summary_col=rule.params["summary_col"], agg=rule.params["agg"],
                                match_rate=rule.match_rate or 0.0, n_rows=rule.n_rows)
            tables[rule.table_id] = apply_aggregate_rule(agg, detail, summary)

    action = "; ".join(dict.fromkeys(actions)) or "no change"
    return replace(data, tables=tables), action, touched
