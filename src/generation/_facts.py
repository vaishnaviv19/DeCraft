# src/generation/_facts.py
"""Facts (M8.2): every number the narrative may use, computed by Python from the Display Tables."""
from __future__ import annotations

import pandas as pd

from src.contracts import Fact, PatternKind
from src.contracts.dataset import CanonicalDataset
from src.core.hierarchy import total_row_links


def _label_column(display: pd.DataFrame) -> str | None:
    for column in display.columns:
        if not pd.api.types.is_numeric_dtype(display[column]):
            return str(column)
    return None


def compute_facts(data: CanonicalDataset, patterns: list | None = None) -> dict[str, Fact]:
    facts: dict[str, Fact] = {}
    trend = {(p.fields[0].table_id, p.fields[0].column): p for p in (patterns or [])
             if p.kind is PatternKind.TREND and p.fields}

    for table_id, display in data.display.items():
        label_col = _label_column(display)
        links = total_row_links(display, label_col)
        leaves = [i for i in range(len(display)) if i not in links]
        for column in display.columns:
            if not pd.api.types.is_numeric_dtype(display[column]):
                continue
            series = pd.to_numeric(display[column], errors="coerce")
            total = float(series.iloc[leaves].sum())
            key = f"{table_id}.{column}.total"
            facts[key] = Fact(key=key, value=total, kind="number",
                              source=f"{table_id}.{column}:sum")
            if not leaves:
                continue
            largest_row = int(series.iloc[leaves].idxmax())
            largest = float(series.iloc[largest_row])
            facts[f"{table_id}.{column}.largest"] = Fact(
                key=f"{table_id}.{column}.largest", value=largest, kind="number",
                source=f"{table_id}.{column}:max")
            if label_col is not None and largest_row < len(display):
                label = str(display[label_col].iloc[largest_row])
                facts[f"{table_id}.{column}.largest_label"] = Fact(
                    key=f"{table_id}.{column}.largest_label", value=label, kind="text",
                    source=f"{table_id}.{column}:argmax")
                if total:
                    facts[f"{table_id}.{column}.largest_share"] = Fact(
                        key=f"{table_id}.{column}.largest_share", value=100.0 * largest / total,
                        kind="number", unit="%", source=f"{table_id}.{column}:argmax/share")
            pattern = trend.get((table_id, str(column)))
            if pattern is not None:
                values = series.iloc[leaves]
                if len(values) >= 2 and values.iloc[0]:
                    growth = 100.0 * (values.iloc[-1] / values.iloc[0] - 1)
                    facts[f"{table_id}.{column}.growth"] = Fact(
                        key=f"{table_id}.{column}.growth", value=float(growth), kind="number",
                        unit="%", source=f"{table_id}.{column}:growth")
                facts[f"{table_id}.{column}.direction"] = Fact(
                    key=f"{table_id}.{column}.direction", value=pattern.direction.value, kind="word",
                    source=f"pattern:{pattern.pattern_id}")
        if label_col is not None:
            labels = display[label_col].dropna().astype(str)
            if len(labels):
                facts[f"{table_id}.first_label"] = Fact(
                    key=f"{table_id}.first_label", value=labels.iloc[0], kind="text",
                    source=f"{table_id}.{label_col}:first")
                facts[f"{table_id}.last_label"] = Fact(
                    key=f"{table_id}.last_label", value=labels.iloc[-1], kind="text",
                    source=f"{table_id}.{label_col}:last")
    return facts
