# src/generation/_charts.py
"""Chart registration (M8.4): one ChartSpec for every chart the Document Plan pairs with a table.

* statements (line items down the side, periods across): 'rows' orientation - each of the largest
  line items is one series across the periods, as a line chart;
* other tables: 'columns' orientation - up to two number columns against the label column, with the
  plan's chart type (bar, or pie for a single non-negative share-like series with few categories);
* titles are written from the column and line-item names, never internal ids.
The data itself is still read from the Display Table when the chart is drawn.
"""
from __future__ import annotations

import pandas as pd

from src.contracts import ChartSpec
from src.core.facts import TOTAL_RE, summable
from src.core.periods import parse_period
from src.core.rows import label_positions, norm_label, number_columns, statement_label_column

MAX_SERIES, MAX_PIE_SLICES = 3, 8


def _label_column(display: pd.DataFrame, profiles: list, keys: set[str]) -> str | None:
    texts = [p.column for p in profiles if p.physical_type == "text" and p.column in display.columns]
    if not texts:                       # no profiles (or none text): first non-numeric column
        texts = [str(c) for c in display.columns if not pd.api.types.is_numeric_dtype(display[c])]
    preferred = [c for c in texts if c not in keys]
    return (preferred or texts or [None])[0]


def _statement_chart(chart_id: str, table_id: str, display: pd.DataFrame, label: str) -> ChartSpec | None:
    periods = sorted(((parse_period(str(c)), c) for c in number_columns(display, label)
                      if parse_period(str(c)) is not None), key=lambda x: x[0].ordinal)
    columns = [c for _, c in periods]
    if len(columns) < 2:
        return None
    latest = pd.to_numeric(display[columns[-1]], errors="coerce").abs()
    once = {k for k, rows in label_positions(display[label]).items() if len(rows) == 1}
    items = [(latest.iloc[i], str(display[label].iloc[i]).strip()) for i in range(len(display))
             if pd.notna(latest.iloc[i]) and str(display[label].iloc[i]).strip()
             and norm_label(display[label].iloc[i]) in once          # a repeated label is ambiguous
             and not TOTAL_RE.search(str(display[label].iloc[i]))]
    amounts = [x for x in items if summable(None, x[1])]          # 'Tax Rate (%)' is not an amount
    rows = [name for _, name in sorted(amounts or items, key=lambda x: -x[0])[:MAX_SERIES]]
    if not rows:
        return None
    title = f"{', '.join(rows[:-1]) + ' and ' + rows[-1] if len(rows) > 1 else rows[0]}, {columns[0]} to {columns[-1]}"
    return ChartSpec(chart_id=chart_id, table_id=table_id, label_column=label, series_columns=columns,
                     orientation="rows", series_rows=rows, kind="line", title=title)


def _column_chart(chart_id: str, table_id: str, display: pd.DataFrame, label: str, profiles: dict,
                  keys: set[str], wanted_kind: str, y_hint: str | None) -> ChartSpec | None:
    numbers = [c for c in display.columns if c != label and c not in keys
               and pd.api.types.is_numeric_dtype(display[c]) and pd.to_numeric(display[c], errors="coerce").notna().any()]
    if not numbers:
        return None
    first = y_hint if y_hint in numbers else next((c for c in numbers if summable(profiles.get(c), c)), numbers[0])
    series = [first] + [c for c in numbers if c != first][:1]
    values = pd.to_numeric(display[first], errors="coerce").dropna()
    kind = "bar"
    if wanted_kind == "pie" and len(display) <= MAX_PIE_SLICES and (values >= 0).all() and summable(profiles.get(first), first):
        kind, series = "pie", [first]
    title = f"{' and '.join(series)} by {label}"
    return ChartSpec(chart_id=chart_id, table_id=table_id, label_column=label, series_columns=series,
                     kind=kind, title=title)


def register_all_charts(data, document_plan: dict | None = None) -> dict[str, ChartSpec]:
    """Every displayed table gets exactly one chart (E-19): the plan's pair when it has one, otherwise
    a spec whose kind follows `choose_chart_kind`."""
    charts = register_charts(data, document_plan)
    hidden = {spec.table_id for spec in charts.values() if not spec.display}
    charted = {spec.table_id for spec in charts.values()}
    for table_id, display in data.display.items():
        if table_id in hidden or table_id in charted:
            continue
        profiles = [p for p in data.source_profiles if p.table_id == table_id]
        keys = set(data.key_columns.get(table_id, []))
        label = _label_column(display, profiles, keys)
        numbers = [str(c) for c in display.columns if c != label
                   and pd.api.types.is_numeric_dtype(display[c])
                   and pd.to_numeric(display[c], errors="coerce").notna().any()]
        if label is None or not numbers:
            continue
        series = numbers[:3]
        unit = next((p.unit for p in profiles if p.column == series[0] and p.unit), None)
        kind = choose_chart_kind(data, table_id, None)
        charts[f"chart_{table_id}"] = ChartSpec(
            chart_id=f"chart_{table_id}", table_id=table_id, label_column=label,
            series_columns=series, kind=kind, orientation="columns",
            title=f"{' and '.join(series)} by {label}", unit=unit,
            has_data_labels=len(display) <= 12)
    return charts


KIND_ALIASES = {"bar": "bar", "horizontal_bar": "horizontal_bar", "stacked_bar": "bar",
                "line": "line", "area": "line", "pie": "pie", "donut": "donut",
                "scatter": "bar", "other": "bar"}


def choose_chart_kind(data, table_id: str, figure_info=None) -> str:
    """The source's own chart kind when known (E-19), otherwise the rules."""
    if figure_info is not None and getattr(figure_info, "kind", None) == "chart" \
            and getattr(figure_info, "chart_kind", None):
        return KIND_ALIASES.get(str(figure_info.chart_kind), "bar")
    display = data.display.get(table_id)
    if display is None:
        display = data.tables.get(table_id)
    if display is None:
        return "bar"
    profiles = [p for p in data.source_profiles if p.table_id == table_id]
    label = _label_column(display, profiles, set(data.key_columns.get(table_id, [])))
    numbers = [c for c in display.columns if c != label
               and pd.api.types.is_numeric_dtype(display[c])]
    periods = [c for c in display.columns if parse_period(str(c)) is not None]
    if label is not None and len(numbers) == 1 and len(display) <= 8:
        total = float(pd.to_numeric(display[numbers[0]], errors="coerce").sum())
        if abs(total - 100.0) <= 0.5:
            return "donut"
    if len(periods) > 3:
        return "line" if len(periods) > 6 else "bar"
    if label is not None and numbers:
        return "horizontal_bar" if len(numbers) <= 3 else "bar"
    return "bar"


def register_charts(data, document_plan: dict | None) -> dict[str, ChartSpec]:
    """ChartSpecs for the plan's charts (every planned table has one); tables the plan does not pair
    get none. Charts whose table was not generated are skipped."""
    charts: dict[str, ChartSpec] = {}
    planned = [c for s in (document_plan or {}).get("sections", []) for c in s.get("charts", []) or []]
    for n, chart in enumerate(planned, start=1):
        table_id = chart.get("data_source_table_id")
        display = data.display.get(table_id)
        if display is None:
            continue
        profiles = [p for p in data.source_profiles if p.table_id == table_id]
        keys = set(data.key_columns.get(table_id, []))
        label = _label_column(display, profiles, keys)
        if label is None:
            continue
        chart_id = str(chart.get("chart_id") or f"chart_{n:03d}")
        spec = _statement_chart(chart_id, table_id, display, label) if statement_label_column(display, label) else None
        if spec is None:
            spec = _column_chart(chart_id, table_id, display, label, {p.column: p for p in profiles}, keys,
                                 str(chart.get("type") or "bar"), chart.get("y_mapping"))
        if spec is not None:
            charts[chart_id] = spec
    return charts
