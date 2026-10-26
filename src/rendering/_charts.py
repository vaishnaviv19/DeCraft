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
    """Plotted numbers from displayed cells. Text cells are read with the shared `parse_number`
    ('Rs. 113,077' -> 113077, '67.5%' -> 67.5, '(5.4)' -> -5.4), so a table whose numbers are stored
    as text still gets a chart, and the chart, its sidecar and the check all read the same values."""
    from src.core.values import parse_number
    out: list[float | None] = []
    for value in list(values):
        if value is None or (isinstance(value, float) and value != value):
            out.append(None)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(float(value))
        else:
            out.append(parse_number(value))
    return out


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
        if spec.x_labels and len(spec.x_labels) == len(spec.series_columns):
            return [str(c) for c in spec.x_labels]
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
        expected = _clean(display[column])
        assert chart[column] == expected, f"chart series {column} differs from the table"
    return True


def assert_sidecar_matches_display(payload: dict, display: pd.DataFrame) -> bool:
    """Raise AssertionError on any plotted value that differs from the Display Table (both
    orientations). A 'rows' sidecar names line items, so its keys are rows of `label_column` and its
    labels are the period columns; a 'columns' sidecar names the series columns themselves."""
    series = payload.get("series", {})
    labels = [str(value) for value in (payload.get("columns") or payload.get("labels", []))]
    if payload.get("orientation") == "rows":
        label_column = payload.get("label_column")
        if label_column not in display.columns:
            raise AssertionError(f"label column {label_column!r} is not in the Display Table")
        missing = [column for column in labels if column not in display.columns]
        if missing:
            raise AssertionError(f"period columns {missing} are not in the Display Table")
        where = label_positions(display[label_column])
        for row, values in series.items():
            positions = where.get(norm_label(row), [])
            if len(positions) != 1:
                raise AssertionError(f"line item {row!r} is not in the Display Table exactly once")
            expected = _clean(display.loc[:, labels].iloc[positions[0]])
            assert values == expected, f"chart series {row} differs from the table"
        return True
    for column, values in series.items():
        if column not in display.columns:
            raise AssertionError(f"series {column!r} is not in the Display Table")
        assert values == _clean(display[column]), f"chart series {column} differs from the table"
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
               "columns": [str(c) for c in spec.series_columns] if spec.orientation == "rows" else [],
               "labels": chart_labels(data, chart_id), "series": chart_series(data, chart_id)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def chart_colours(palette: list[str], count: int = 6) -> list[str]:
    """Series colours from the source palette, in palette order (M8.5.2), keeping only colours that
    stand out on white (contrast >= 2.2: pale panel greys made invisible lines); when too few remain,
    darker and lighter shades of the first one are added."""
    def luminance(hex_colour: str) -> float:
        value = hex_colour.lstrip("#")[:6].ljust(6, "0")
        channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    usable = [c for c in palette if c.startswith("#") and 1.05 / (luminance(c) + 0.05) >= 2.2]
    base = usable[0] if usable else "#1F3864"
    shades = []
    r, g, b = (int(base.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    for factor in (0.6, 1.35, 0.4, 1.6):
        shade = "#{:02X}{:02X}{:02X}".format(*(max(0, min(255, int(c * factor))) for c in (r, g, b)))
        if 1.05 / (luminance(shade) + 0.05) >= 2.2:
            shades.append(shade)
    out = list(dict.fromkeys(usable + shades + ["#595959", "#A6A6A6"]))
    return out[:count]


def draw_svg(data: CanonicalDataset, chart_id: str, path: Path, colors: list[str] | None = None,
             font: str | None = None, font_pt: float = 10.0, width_pt: float = 302.0,
             height_pt: float | None = None) -> Path:
    return _draw_chart(data, chart_id, path, "svg", colors, font, font_pt, width_pt, height_pt)


def draw_png(data: CanonicalDataset, chart_id: str, path: Path, colors: list[str] | None = None,
             font: str | None = None, font_pt: float = 10.0, width_pt: float = 302.0,
             height_pt: float | None = None) -> Path:
    """A raster copy for the contact sheet; the SVG stays the rendered artefact."""
    return _draw_chart(data, chart_id, path, "png", colors, font, font_pt, width_pt, height_pt)


def _draw_chart(data: CanonicalDataset, chart_id: str, path: Path, fmt: str,
                colors: list[str] | None, font: str | None, font_pt: float, width_pt: float,
                height_pt: float | None = None) -> Path:
    """E-38 Font Lock: the chart is drawn at its displayed width (`width_pt`) and every text uses
    `font_pt` (the source body size), so nothing is scaled when the page shows it. Text stays text."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    spec = registered(data, chart_id)
    series = chart_series(data, chart_id)
    labels = chart_labels(data, chart_id)
    palette = list(spec.colors) or colors or None                 # F-06: the source chart's colours first
    sizes = {k: font_pt for k in ("font.size", "axes.titlesize", "axes.labelsize", "xtick.labelsize",
                                  "ytick.labelsize", "legend.fontsize")}
    with plt.rc_context({"font.family": [font, "DejaVu Sans"] if font else ["DejaVu Sans"],
                         "svg.fonttype": "none", **sizes}):
        # M8.5.2: the figure is the chart block's box (pt / 72 = inches), so the page never scales it
        figure, axes = plt.subplots(figsize=(width_pt / 72.0, (height_pt or width_pt * 0.62) / 72.0))
        # crowded category axes (narrow box, many labels) turn their labels upright
        longest = max((len(str(label)) for label in labels), default=1)
        tick_rotation = 90 if width_pt / max(len(labels), 1) < font_pt * 0.6 * min(longest, 8) else 30
        if spec.kind in {"pie", "donut"}:
            values = [v or 0.0 for v in next(iter(series.values()))]
            axes.pie(values, labels=labels, colors=palette, startangle=90, counterclock=False,
                     wedgeprops={"width": 0.45} if spec.kind == "donut" else None)
            axes.axis("equal")
        elif spec.kind == "horizontal_bar":
            height = 0.8 / max(len(series), 1)
            for k, (name, values) in enumerate(series.items()):
                points = [float("nan") if v is None else v for v in values]
                axes.barh([i + k * height for i in range(len(points))], points, height=height,
                          label=name, color=(palette[k % len(palette)] if palette else None))
                if spec.has_data_labels:
                    for i, point in enumerate(points):
                        if point == point:
                            axes.text(point, i + k * height, f"{point:g}", va="center", fontsize=font_pt)
            axes.set_yticks([i + height * (len(series) - 1) / 2 for i in range(len(labels))])
            axes.set_yticklabels(labels)
        elif spec.kind == "bar":
            width = 0.8 / max(len(series), 1)
            for k, (name, values) in enumerate(series.items()):
                points = [float("nan") if v is None else v for v in values]
                axes.bar([i + k * width for i in range(len(points))], points, width=width, label=name,
                         color=(palette[k % len(palette)] if palette else None))
                if spec.has_data_labels:
                    for i, point in enumerate(points):
                        if point == point:
                            axes.text(i + k * width, point, f"{point:g}", ha="center",
                                      va="bottom", fontsize=font_pt)
            axes.set_xticks([i + width * (len(series) - 1) / 2 for i in range(len(labels))])
            axes.set_xticklabels(labels, rotation=tick_rotation, ha="right" if tick_rotation < 90 else "center")
        else:
            for k, (name, values) in enumerate(series.items()):
                points = [float("nan") if v is None else v for v in values]
                axes.plot(range(len(points)), points, marker="o" if spec.markers else None, label=name,
                          color=(palette[k % len(palette)] if palette else None))
                if spec.has_data_labels:
                    for i, point in enumerate(points):
                        if point == point:
                            axes.annotate(f"{point:g}", (i, point), textcoords="offset points",
                                          xytext=(0, 4), ha="center", fontsize=font_pt)
            axes.set_xticks(range(len(labels)))
            axes.set_xticklabels(labels, rotation=tick_rotation, ha="right" if tick_rotation < 90 else "center")
        import textwrap

        from matplotlib.ticker import MaxNLocator
        legend = spec.kind not in {"pie", "donut"} and len(series) > 1 and spec.has_legend
        # a small box (a 40% split under a table, a source chart frame) gets a COMPACT chart: no title
        # (the heading above names it; a recovered chart's legend names its series), one legend row,
        # at most three value ticks. The font size never changes (E-29); the marks get the room.
        compact = (height_pt or width_pt * 0.62) < 110 or (spec.title is None)
        fit_row = max(1, int(width_pt // (font_pt * 9)))      # legend entries that fit one row
        per_row = min(len(series), fit_row) if compact else (3 if width_pt >= 260 else 2)
        legend_rows = -(-len(series) // max(per_row, 1)) if legend else 0
        if compact:
            if legend:
                axes.set_title(" ", pad=font_pt * 1.4 * legend_rows)
            axes.yaxis.set_major_locator(MaxNLocator(3))
            if spec.kind not in {"pie", "donut", "horizontal_bar"} and                     width_pt / max(len(labels), 1) >= font_pt * 0.6 * max((len(str(x)) for x in labels), default=1):
                axes.tick_params(axis="x", labelrotation=0)
        else:
            title = textwrap.fill(spec.title or chart_id, max(12, int(width_pt / (font_pt * 0.55))))
            # the legend sits between the title and the plot, so it never covers a line or bar
            axes.set_title(title, pad=(font_pt * 1.6 * legend_rows + 6) if legend else 6)
        if spec.unit and not compact:
            axes.set_ylabel(spec.unit)
        if spec.kind not in {"pie", "donut"}:
            axes.spines[["top", "right"]].set_visible(False)
            if legend:
                axes.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=min(len(series), per_row),
                            frameon=False, borderaxespad=0.2, handlelength=1.4, columnspacing=1.0)
        figure.tight_layout(pad=0.4)
        path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(path, format=fmt, dpi=110)
        plt.close(figure)
    return path


def contact_sheet(paths: list[Path], target: Path, columns: int = 3) -> Path | None:
    """Tile the raster copies into one sheet for the M8.5 check; skipped when Pillow is missing."""
    if not paths:
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    images = [Image.open(path).convert("RGB") for path in paths]
    width = max(image.width for image in images)
    height = max(image.height for image in images)
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * width, rows * height), "white")
    for index, image in enumerate(images):
        sheet.paste(image, ((index % columns) * width, (index // columns) * height))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target)
    return target
