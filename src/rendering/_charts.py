# src/rendering/_charts.py
"""Charts (M8.4): a chart can only be drawn from a registered Chart Spec, and its data is read from the
Display Table. A JSON sidecar records exactly what was plotted so QA can compare it with the table."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.contracts import ChartSpec
from src.contracts.dataset import CanonicalDataset


def registered(data: CanonicalDataset, chart_id: str) -> ChartSpec:
    spec = data.charts.get(chart_id)
    if spec is None:
        raise KeyError(f"chart {chart_id!r} is not registered")
    return spec


def chart_series(data: CanonicalDataset, chart_id: str) -> dict[str, list[float | None]]:
    spec = registered(data, chart_id)
    display = data.display.get(spec.table_id)
    if display is None:
        display = data.tables.get(spec.table_id)
    if display is None:
        raise KeyError(f"table {spec.table_id!r} is missing")
    series: dict[str, list[float | None]] = {}
    for column in spec.series_columns:
        if column not in display.columns:
            raise KeyError(f"series {column!r} is not in the Display Table")
        values = pd.to_numeric(display[column], errors="coerce")
        series[column] = [None if pd.isna(v) else float(v) for v in values]
    return series


def chart_labels(data: CanonicalDataset, chart_id: str) -> list[str]:
    spec = registered(data, chart_id)
    display = data.display.get(spec.table_id)
    if display is None:
        display = data.tables.get(spec.table_id)
    if display is None or spec.label_column not in display.columns:
        raise KeyError(f"label column {spec.label_column!r} is not in the Display Table")
    return [str(v) for v in display[spec.label_column]]


def assert_chart_matches_table(chart: dict, display: pd.DataFrame, columns: list[str]) -> bool:
    """Raise AssertionError on any series value that differs from the Display Table (a Gate)."""
    for column in columns:
        expected = [None if pd.isna(v) else float(v) for v in pd.to_numeric(display[column], errors="coerce")]
        assert chart[column] == expected, f"chart series {column} differs from the table"
    return True


def write_sidecar(data: CanonicalDataset, chart_id: str, path: Path) -> Path:
    spec = registered(data, chart_id)
    payload = {"chart_id": chart_id, "table_id": spec.table_id, "label_column": spec.label_column,
               "labels": chart_labels(data, chart_id), "series": chart_series(data, chart_id)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def draw_svg(data: CanonicalDataset, chart_id: str, path: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    spec = registered(data, chart_id)
    series = chart_series(data, chart_id)
    labels = chart_labels(data, chart_id)
    figure, axes = plt.subplots(figsize=(6.4, 3.2))
    for column, values in series.items():
        points = [float("nan") if v is None else v for v in values]
        axes.plot(range(len(points)), points, marker="o", label=column)
    axes.set_xticks(range(len(labels)))
    axes.set_xticklabels(labels, rotation=45, ha="right")
    axes.set_title(spec.title or spec.chart_id)
    axes.set_ylabel(", ".join(spec.series_columns))
    axes.legend()
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, format="svg")
    plt.close(figure)
    return path
