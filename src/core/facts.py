# src/core/facts.py
"""Facts (M8.2, Shared Kernel): every number the narrative may use, computed by Python from Display
Tables. Used for the generated report AND for the source (the privacy headline check compares the two
with the same definitions).

* plain tables: per column a total (only for columns that add up) or an average (rates, prices,
  percentages), the largest value and its label, growth and direction in TIME order for trends;
* statements (line items down the side, periods across): per line item its latest value, growth from
  the first to the last period and its direction; the first and last period labels;
* names (columns, line items, periods) are text facts, so templates never contain raw digits.
"""
from __future__ import annotations

import re

import pandas as pd

from src.contracts import Fact, PatternKind
from src.core.hierarchy import total_row_links
from src.core.periods import parse_period, time_order
from src.core.rows import label_positions, norm_label, number_columns, statement_label_column

NOT_SUMMABLE = re.compile(r"%|\b(rate|ratio|margin|yield|price|avg|average|per|share|growth|index|score|"
                          r"age|years?|percent|multiple|eps|p/e)\b", re.I)
CURRENCY = {"USD": "$", "INR": "\u20b9", "EUR": "\u20ac", "GBP": "\u00a3", "$": "$"}
RATE_ROW = re.compile(r"%|\b(rate|ratio|margin|yield|change|growth)\b", re.I)
TOTAL_RE = re.compile(r"\b(sub ?|grand )?total\b", re.I)


def unit_of(profile, column: str) -> str | None:
    unit = getattr(profile, "unit", None)
    if unit == "%" or "%" in str(column):
        return "%"
    return CURRENCY.get(unit) if unit else None


def summable(profile, column: str) -> bool:
    if unit_of(profile, column) == "%" or NOT_SUMMABLE.search(str(column)):
        return False
    return getattr(profile, "semantic_type", "") not in ("percentage", "identifier", "ratio")


def _num(key, value, source, profile=None, column="", unit=None):
    return Fact(key=key, value=float(value), kind="number", source=source,
                unit=unit if unit is not None else unit_of(profile, column),
                decimals=getattr(profile, "decimals", None))


def _text(key, value, source):
    return Fact(key=key, value=str(value), kind="text", source=source)


def _statement_facts(table_id, display, label, profiles, row_trend, facts):
    periods = sorted(((parse_period(str(c)), c) for c in number_columns(display, label)
                      if parse_period(str(c)) is not None), key=lambda x: x[0].ordinal)
    columns = [c for _, c in periods]
    if not columns:
        return
    facts[f"{table_id}.first_period"] = _text(f"{table_id}.first_period", columns[0], f"{table_id}:header")
    facts[f"{table_id}.last_period"] = _text(f"{table_id}.last_period", columns[-1], f"{table_id}:header")
    repeated = {k for k, rows in label_positions(display[label]).items() if len(rows) > 1}
    for i, row_label in enumerate(display[label]):
        if row_label is None or not str(row_label).strip() or TOTAL_RE.search(str(row_label)) \
                or norm_label(row_label) in repeated:          # a repeated label is ambiguous
            continue
        name = str(row_label).strip()
        rate = bool(RATE_ROW.search(name))                     # e.g. Tax Rate (%), % change, margin
        base = f"{table_id}.row:{name}"
        values = [pd.to_numeric(display[c].iloc[i], errors="coerce") for c in columns]
        present = [(c, v) for c, v in zip(columns, values) if pd.notna(v)]
        if not present:
            continue
        profile = profiles.get(present[-1][0])
        facts[f"{base}.label"] = _text(f"{base}.label", name, f"{table_id}.{label}:row")
        facts[f"{base}.latest"] = _num(f"{base}.latest", present[-1][1], f"{table_id}.row:{name}:latest",
                                       profile, present[-1][0],
                                       unit=("%" if "%" in name or rate else None) if rate else None)
        # statement cells carry no currency symbol, so neither does the text (it must match the table)
        facts[f"{base}.latest"] = facts[f"{base}.latest"].model_copy(update={"unit": "%" if "%" in name else None})
        signs_agree = len(present) >= 2 and present[0][1] * present[-1][1] > 0
        if signs_agree and not rate:                           # a sign change has no meaningful growth %
            growth = 100.0 * (present[-1][1] / present[0][1] - 1)
            facts[f"{base}.growth"] = Fact(key=f"{base}.growth", value=float(growth), kind="number", unit="%",
                                           decimals=1, source=f"{table_id}.row:{name}:growth")
        pattern = row_trend.get((table_id, norm_label(name)))
        if pattern is not None:
            facts[f"{base}.direction"] = Fact(key=f"{base}.direction", value=pattern.direction.value,
                                              kind="word", source=f"pattern:{pattern.pattern_id}")


def compute_facts(data, patterns: list | None = None) -> dict[str, Fact]:
    """`data` needs .display, .source_profiles and .key_columns (a CanonicalDataset)."""
    facts: dict[str, Fact] = {}
    col_trend = {(p.fields[0].table_id, p.fields[0].column): p for p in (patterns or [])
                 if p.kind is PatternKind.TREND and p.fields and not p.row_label}
    row_trend = {(p.fields[0].table_id, norm_label(p.row_label)): p for p in (patterns or [])
                 if p.kind is PatternKind.TREND and p.fields and p.row_label}
    for table_id, display in data.display.items():
        table_profiles = [p for p in getattr(data, "source_profiles", []) if p.table_id == table_id]
        profiles = {p.column: p for p in table_profiles}
        texts = [c for c in display.columns if not pd.api.types.is_numeric_dtype(display[c])]
        label = next((p.column for p in table_profiles if p.physical_type == "text" and p.column in display),
                     texts[0] if texts else None)
        if statement_label_column(display, label):
            _statement_facts(table_id, display, label, profiles, row_trend, facts)
            continue
        keys = set(getattr(data, "key_columns", {}).get(table_id, []))
        time_col = next((p.column for p in table_profiles if p.physical_type in {"period", "date"}
                         and p.column in display.columns), None)
        links = total_row_links(display, label)
        order = time_order(display[time_col]) if time_col else list(range(len(display)))
        leaves = [i for i in order if i not in links]
        for column in display.columns:
            if column in keys or column == time_col or not pd.api.types.is_numeric_dtype(display[column]):
                continue
            profile = profiles.get(column)
            series = pd.to_numeric(display[column], errors="coerce")
            values = series.iloc[leaves]
            if values.notna().sum() == 0:
                continue
            base = f"{table_id}.{column}"
            facts[f"{base}.name"] = _text(f"{base}.name", column, f"{table_id}:header")
            if summable(profile, column):
                total = float(values.sum())
                facts[f"{base}.total"] = _num(f"{base}.total", total, f"{table_id}.{column}:sum", profile, column)
            else:
                total = None
                facts[f"{base}.average"] = _num(f"{base}.average", float(values.mean()),
                                                f"{table_id}.{column}:mean", profile, column)
            largest_row = int(values.idxmax())
            facts[f"{base}.largest"] = _num(f"{base}.largest", series.iloc[largest_row],
                                            f"{table_id}.{column}:max", profile, column)
            if label is not None:
                facts[f"{base}.largest_label"] = _text(f"{base}.largest_label", display[label].iloc[largest_row],
                                                       f"{table_id}.{column}:argmax")
                if total:
                    facts[f"{base}.largest_share"] = Fact(
                        key=f"{base}.largest_share", value=100.0 * float(series.iloc[largest_row]) / total,
                        kind="number", unit="%", decimals=1, source=f"{table_id}.{column}:argmax/share")
            pattern = col_trend.get((table_id, str(column)))
            if pattern is not None:
                ordered = [v for v in values if pd.notna(v)]               # time order, totals excluded
                if len(ordered) >= 2 and ordered[0]:
                    facts[f"{base}.growth"] = Fact(
                        key=f"{base}.growth", value=float(100.0 * (ordered[-1] / ordered[0] - 1)), kind="number",
                        unit="%", decimals=1, source=f"{table_id}.{column}:growth")
                facts[f"{base}.direction"] = Fact(key=f"{base}.direction", value=pattern.direction.value,
                                                  kind="word", source=f"pattern:{pattern.pattern_id}")
        if time_col is not None:
            periods = [display[time_col].iloc[i] for i in order if pd.notna(display[time_col].iloc[i])]
            if periods:
                facts[f"{table_id}.first_period"] = _text(f"{table_id}.first_period", periods[0], f"{table_id}.{time_col}:first")
                facts[f"{table_id}.last_period"] = _text(f"{table_id}.last_period", periods[-1], f"{table_id}.{time_col}:last")
        if label is not None:
            labels = display[label].dropna().astype(str)
            if len(labels):
                facts[f"{table_id}.first_label"] = _text(f"{table_id}.first_label", labels.iloc[0], f"{table_id}.{label}:first")
                facts[f"{table_id}.last_label"] = _text(f"{table_id}.last_label", labels.iloc[-1], f"{table_id}.{label}:last")
    return facts
