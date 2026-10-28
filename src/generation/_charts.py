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


def text_number_chart(chart_id: str, table_id: str, display: pd.DataFrame, label: str) -> ChartSpec | None:
    """A chart for a table whose numbers are text cells, typically several small tables stacked in
    one grid ('Shareholding % | Q3FY24 | Q4FY24 | Q1FY25', then 'Promoters | 49.4 | 58.7 | 59.9'):
    the largest group of uniquely labelled rows that are numbers in the SAME columns (at least two)
    and written in the same form (all percentages or none) becomes 'rows' series across those columns,
    so every plotted value is a cell of the table (task 7). None when no such group exists."""
    from src.core.values import parse_number

    columns = [str(c) for c in display.columns if c != label]
    once = {k for k, rows in label_positions(display[label]).items() if len(rows) == 1}
    groups: dict[tuple, list[str]] = {}
    for i in range(len(display)):
        name = str(display[label].iloc[i]).strip()
        if not name or name.lower() == "nan" or norm_label(name) not in once or TOTAL_RE.search(name):
            continue
        cells = {c: display[c].iloc[i] for c in columns}
        numeric = tuple(c for c, v in cells.items() if v is not None and str(v).strip()
                        and str(v).strip().lower() != "nan" and parse_number(v) is not None)
        if len(numeric) < 2:
            continue
        percent = all("%" in str(cells[c]) for c in numeric)
        groups.setdefault((numeric, percent), []).append(name)
    if not groups:
        return None
    (series_columns, _), rows = max(groups.items(), key=lambda item: (len(item[1]), len(item[0][0])))
    if len(rows) < 2:
        return None
    series_columns, rows = _one_run(display, label, list(series_columns), rows)
    if len(rows) < 2:
        return None
    rows = rows[:5]
    # the sub-header row nearest above the first series row names the columns ('Shareholding % |
    # Q3FY24 | Q4FY24 | Q1FY25'); its cells become the axis labels instead of 'Unnamed: 1'
    x_labels = _sub_header(display, label, list(series_columns), rows[0])
    title = f"{', '.join(rows[:-1])} and {rows[-1]}"
    return ChartSpec(chart_id=chart_id, table_id=table_id, label_column=label,
                     series_columns=list(series_columns), orientation="rows", series_rows=rows,
                     kind="bar", title=title, x_labels=x_labels)


def _sub_header(display: pd.DataFrame, label: str, columns: list[str], first_row: str) -> list[str]:
    from src.core.values import parse_number
    first = next((i for i in range(len(display)) if str(display[label].iloc[i]).strip() == first_row), 0)
    for i in range(first - 1, -1, -1):
        cells = [str(display[c].iloc[i]).strip() for c in columns]
        if all(cell and cell.lower() != "nan" and parse_number(cell) is None for cell in cells):
            return cells
    return []


def _one_run(display: pd.DataFrame, label: str, columns: list[str], rows: list[str]):
    """A table with column groups ('Old estimates | New estimates | Change %', each FY25E FY26E) is
    one axis per group: the axis is the LAST group of the largest values (the new figures, not the %
    change), and the rows are the largest amounts of a similar size (Revenue, EBITDA, Adj. PAT - not
    beside the margin or the EPS). Before, all six columns were one axis."""
    from src.core.values import parse_number
    headers = _sub_header(display, label, columns, rows[0])
    if headers and len(set(headers)) < len(headers):
        runs, current = [], []
        for column, head in zip(columns, headers):
            if head in [headers[columns.index(c)] for c in current]:
                runs.append(current)
                current = []
            current.append(column)
        runs.append(current)
        where = {str(display[label].iloc[i]).strip(): i for i in range(len(display))}

        def size(run):
            values = [abs(v) for r in rows for c in run
                      if (v := parse_number(display[c].iloc[where[r]])) is not None]
            return float(pd.Series(values).median()) if values else 0.0
        biggest = max(size(run) for run in runs)
        columns = [run for run in runs if size(run) >= 0.5 * biggest][-1]
    where = {str(display[label].iloc[i]).strip(): i for i in range(len(display))}
    amounts = []
    for r in rows:
        values = [parse_number(display[c].iloc[where[r]]) for c in columns]
        values = [abs(v) for v in values if v is not None]
        if values and "%" not in r:
            amounts.append((max(values), r))
    if len(amounts) >= 2 and max(v for v, _ in amounts) > 1000 * max(min(v for v, _ in amounts), 1e-9):
        top = max(v for v, _ in amounts)               # amounts beside per-share figures: amounts only
        rows = [r for v, r in sorted(amounts, reverse=True) if v >= top / 20][:3]
        rows = sorted(rows, key=lambda r: where[r])
    return columns, rows


def _column_chart(chart_id: str, table_id: str, display: pd.DataFrame, label: str, profiles: dict,
                  keys: set[str], wanted_kind: str, y_hint: str | None) -> ChartSpec | None:
    numbers = [c for c in display.columns if c != label and c not in keys
               and pd.api.types.is_numeric_dtype(display[c]) and pd.to_numeric(display[c], errors="coerce").notna().any()]
    if not numbers:
        return None
    first = y_hint if y_hint in numbers else next((c for c in numbers if summable(profiles.get(c), c)), numbers[0])
    series = _similar_scale(display, [first] + [c for c in numbers if c != first])[:2]
    values = pd.to_numeric(display[first], errors="coerce").dropna()
    kind = "bar"
    if wanted_kind == "pie" and len(display) <= MAX_PIE_SLICES and (values >= 0).all() and summable(profiles.get(first), first):
        kind, series = "pie", [first]
    title = f"{' and '.join(series)} by {label}"
    return ChartSpec(chart_id=chart_id, table_id=table_id, label_column=label, series_columns=series,
                     kind=kind, title=title)


def _scale(display: pd.DataFrame, column) -> float:
    from src.core.values import parse_number
    values = [abs(v) for v in (parse_number(x) if isinstance(x, str) else x for x in display[column])
              if v is not None and v == v]
    return float(pd.Series(values).median()) if values else 0.0


def _similar_scale(display: pd.DataFrame, columns: list, ratio: float = 20.0) -> list:
    """The first column and the others of a comparable size: 'Multiple' (~3) beside 'Value (Rs. cr.)'
    (~20,000) drew as an invisible flat line."""
    if not columns:
        return []
    base = _scale(display, columns[0]) or 1.0
    return [columns[0]] + [c for c in columns[1:] if 1 / ratio <= (_scale(display, c) or 0) / base <= ratio]


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
        if label is None:
            continue
        if not numbers:                  # numbers stored as text ('Rs. 86,856', '46.9'): line items
            spec = text_number_chart(f"chart_{table_id}", table_id, display, label)
            if spec is not None:
                charts[spec.chart_id] = spec
            continue
        series = _similar_scale(display, numbers)[:3]
        unit = next((p.unit for p in profiles if p.column == series[0] and p.unit), None)
        kind = choose_chart_kind(data, table_id, None)
        dated = next((p.column for p in profiles if p.physical_type == "date" and p.column in display.columns), None)
        if dated is not None:          # a dated history (target price by date) is a line over time
            label, kind = dated, "line"
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
