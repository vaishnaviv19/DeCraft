# src/rendering/_charts.py
"""Charts (M8.4): a chart can only be drawn from a registered Chart Spec, and its data is read from the
Display Table. A JSON sidecar records exactly what was plotted so QA can compare it with the table.

Orientation 'columns': one series per column, x = the label column. Orientation 'rows' (statements):
one series per line item, x = the period columns in order."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.contracts import ChartSpec
from src.contracts.dataset import CanonicalDataset
from src.core.rows import label_positions, norm_label


def registered(data: CanonicalDataset, chart_id: str) -> ChartSpec:
    spec = data.charts.get(chart_id)
    if spec is None:
        raise KeyError(f"chart {chart_id!r} is not registered")
    return spec


def _display(data: CanonicalDataset, spec: ChartSpec) -> pd.DataFrame:
    display = data.display.get(spec.table_id)
    if display is None:
        display = data.tables.get(spec.table_id)
    if display is None:
        raise KeyError(f"table {spec.table_id!r} is missing")
    return display


def _clean(values) -> list[float | None]:
    return [None if pd.isna(v) else float(v) for v in pd.to_numeric(pd.Series(list(values)), errors="coerce")]


def chart_series(data: CanonicalDataset, chart_id: str) -> dict[str, list[float | None]]:
    spec = registered(data, chart_id)
    display = _display(data, spec)
    for column in spec.series_columns:
        if column not in display.columns:
            raise KeyError(f"series {column!r} is not in the Display Table")
    if spec.orientation == "rows":
        if spec.label_column not in display.columns:
            raise KeyError(f"label column {spec.label_column!r} is not in the Display Table")
        where = label_positions(display[spec.label_column])
        series = {}
        for row in spec.series_rows:
            positions = where.get(norm_label(row), [])
            if len(positions) != 1:
                raise KeyError(f"line item {row!r} is not in the Display Table exactly once")
            series[row] = _clean(display.loc[:, spec.series_columns].iloc[positions[0]])
        return series
    return {column: _clean(display[column]) for column in spec.series_columns}


def chart_labels(data: CanonicalDataset, chart_id: str) -> list[str]:
    spec = registered(data, chart_id)
    if spec.orientation == "rows":
        return [str(c) for c in spec.series_columns]
    display = _display(data, spec)
    if spec.label_column not in display.columns:
        raise KeyError(f"label column {spec.label_column!r} is not in the Display Table")
    return [str(v) for v in display[spec.label_column]]


def chart_caption(spec: ChartSpec) -> str:
    return spec.title or f"{' and '.join(spec.series_columns)} by {spec.label_column}"


def assert_chart_matches_table(chart: dict, display: pd.DataFrame, columns: list[str]) -> bool:
    """Raise AssertionError on any series value that differs from the Display Table (a Gate)."""
    for column in columns:
        expected = [None if pd.isna(v) else float(v) for v in pd.to_numeric(display[column], errors="coerce")]
        assert chart[column] == expected, f"chart series {column} differs from the table"
    return True


def sidecar_matches(data: CanonicalDataset, chart_id: str, path: Path) -> bool:
    """The sidecar on disk equals what the Display Table says now (both orientations)."""
    if not Path(path).exists():
        return False
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload.get("series") == chart_series(data, chart_id) and payload.get("labels") == chart_labels(data, chart_id)


def write_sidecar(data: CanonicalDataset, chart_id: str, path: Path) -> Path:
    spec = registered(data, chart_id)
    payload = {"chart_id": chart_id, "table_id": spec.table_id, "label_column": spec.label_column,
               "kind": spec.kind, "orientation": spec.orientation,
               "labels": chart_labels(data, chart_id), "series": chart_series(data, chart_id)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def draw_svg(data: CanonicalDataset, chart_id: str, path: Path, colors: list[str] | None = None,
             font: str | None = None, font_pt: float = 10.0, width_pt: float = 302.0) -> Path:
    """E-38 Font Lock: the chart is drawn at its displayed width (`width_pt`) and every text uses
    `font_pt` (the source body size), so nothing is scaled when the page shows it. Text stays text."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    spec = registered(data, chart_id)
    series = chart_series(data, chart_id)
    labels = chart_labels(data, chart_id)
    palette = colors or None
    sizes = {k: font_pt for k in ("font.size", "axes.titlesize", "axes.labelsize", "xtick.labelsize",
                                  "ytick.labelsize", "legend.fontsize")}
    with plt.rc_context({"font.family": [font, "DejaVu Sans"] if font else ["DejaVu Sans"],
                         "svg.fonttype": "none", **sizes}):
        figure, axes = plt.subplots(figsize=(width_pt / 72.0, width_pt * 0.62 / 72.0))
        if spec.kind == "pie":
            values = [v or 0.0 for v in next(iter(series.values()))]
            axes.pie(values, labels=labels, colors=palette, startangle=90, counterclock=False)
            axes.axis("equal")
        elif spec.kind == "bar":
            width = 0.8 / max(len(series), 1)
            for k, (name, values) in enumerate(series.items()):
                points = [float("nan") if v is None else v for v in values]
                axes.bar([i + k * width for i in range(len(points))], points, width=width, label=name,
                         color=(palette[k % len(palette)] if palette else None))
            axes.set_xticks([i + width * (len(series) - 1) / 2 for i in range(len(labels))])
            axes.set_xticklabels(labels, rotation=30, ha="right")
        else:
            for k, (name, values) in enumerate(series.items()):
                points = [float("nan") if v is None else v for v in values]
                axes.plot(range(len(points)), points, marker="o", label=name,
                          color=(palette[k % len(palette)] if palette else None))
            axes.set_xticks(range(len(labels)))
            axes.set_xticklabels(labels, rotation=30, ha="right")
        if spec.kind != "pie":
            axes.spines[["top", "right"]].set_visible(False)
            if len(series) > 1:
                axes.legend(frameon=False)
        figure.tight_layout()
        path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(path, format="svg")
        plt.close(figure)
    return path
