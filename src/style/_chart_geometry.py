# src/style/_chart_geometry.py   (v3.3 F-06 chart style, F-07 exact data recovery for vector charts)
"""Vector charts are drawn with rectangles, lines and text in the PDF, so their style AND their values can be
read exactly from geometry. The vision model is only used when this fails (raster charts, log or unlabelled
axes). All functions are pure over simple primitives; `primitives_from_page` adapts PyMuPDF output.

Adapted from the spec's Appendix T.4: the clip in force is honoured (a price line can run past its frame),
and line charts are recovered too (value of each polyline at every x-axis tick label)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

NUM = re.compile(r"^[\s(]*[-+]?[$€£₹]?\s*\d[\d,]*(\.\d+)?\s*%?[)]?$")
MIN_PATH_POINTS = 7                    # a polyline with more pieces than a rule or an arrow


@dataclass
class Prims:
    rects: list[tuple[tuple[float, float, float, float], str | None]] = field(default_factory=list)  # bbox, fill
    lines: list[tuple[tuple[float, float, float, float], float]] = field(default_factory=list)       # x0,y0,x1,y1,w
    texts: list[tuple[str, tuple[float, float, float, float], float]] = field(default_factory=list)  # text,bbox,size
    paths: list[tuple[list[tuple[float, float]], str | None]] = field(default_factory=list)         # polyline, colour
    marks: list[tuple[tuple[float, float, float, float], str | None]] = field(default_factory=list)  # legend swatches


def _hex(colour) -> str | None:
    if not colour:
        return None
    return "#%02x%02x%02x" % tuple(int(round(255 * c)) for c in colour[:3])


def primitives_from_page(page, clip) -> Prims:
    import pymupdf
    clip = pymupdf.Rect(clip)
    text_clip = pymupdf.Rect(clip.x0 - 20, clip.y0 - 6, clip.x1 + 6, clip.y1 + 14)   # whole tick labels
    p = Prims()
    clips: dict[int, tuple] = {}
    for d in page.get_drawings(extended=True):
        level = int(d.get("level", 0) or 0)
        if d.get("type") == "clip":
            s = d.get("scissor")
            if s is not None:
                clips = {k: v for k, v in clips.items() if k < level}
                clips[level] = (s.x0, s.y0, s.x1, s.y1)
            continue
        if d.get("type") == "group" or "items" not in d:
            continue
        r = pymupdf.Rect(d["rect"])
        if not clip.intersects(pymupdf.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1)):   # lines: 0-height rects
            continue
        vis = [clip.x0, clip.y0, clip.x1, clip.y1]                  # what is visible: the clip in force
        for k, (x0, y0, x1, y1) in clips.items():
            if k < level:
                vis = [max(vis[0], x0), max(vis[1], y0), min(vis[2], x1), min(vis[3], y1)]
        fill = _hex(d.get("fill"))
        items = d["items"]
        segments = [it for it in items if it[0] == "l"]
        sloped = any(abs(a.x - b.x) > 0.5 and abs(a.y - b.y) > 0.5 for _, a, b in segments)
        if len(segments) >= MIN_PATH_POINTS or (len(segments) >= 3 and sloped):   # a plotted line
            points = [(segments[0][1].x, segments[0][1].y)] + [(it[2].x, it[2].y) for it in segments]
            points = [(x, y) for x, y in points if vis[0] - 0.5 <= x <= vis[2] + 0.5 and vis[1] - 0.5 <= y <= vis[3] + 0.5]
            if len(points) >= 2:
                p.paths.append((points, _hex(d.get("color"))))
            continue
        stroke = _hex(d.get("color"))
        if segments and len(segments) <= 3 and r.width <= 40 and r.height <= 3 and stroke:
            p.marks.append(((r.x0, r.y0, r.x1, r.y1), stroke))     # a legend line sample
        if len(items) == 1 and items[0][0] == "re" and fill and r.width <= 14 and r.height <= 14:
            p.marks.append(((r.x0, r.y0, r.x1, r.y1), fill))       # a legend colour box
        for it in items:
            if it[0] == "re" and fill:
                p.rects.append((tuple(it[1]), fill))
            elif it[0] == "l":
                a, b = it[1], it[2]
                p.lines.append(((a.x, a.y, b.x, b.y), d.get("width") or 1.0))
    for b in page.get_text("dict", clip=text_clip)["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                if s["text"].strip():
                    p.texts.append((s["text"].strip(), tuple(s["bbox"]), s["size"]))
    return p


def _num(t: str) -> float | None:
    if not NUM.match(t):
        return None
    try:
        return float(re.sub(r"[^\d.\-]", "", t.replace("(", "-")))
    except ValueError:
        return None


@dataclass
class ChartStyle:
    orientation: str            # 'vertical' | 'horizontal' | 'line' | 'none'
    n_bars: int
    bar_width_ratio: float      # bar width / category pitch
    gridlines: int              # thin lines spanning the plot width
    has_axis_line: bool
    tick_font_size: float | None
    has_data_labels: bool
    colors: list[str]


def _bars(p: Prims):
    """Coloured rectangles narrower than the chart: white or near-white panels and the plot background
    are not bars (live sample: a white frame sent a line chart down the bar path)."""
    if not p.rects:
        return []
    span = max(b[2] for b, _ in p.rects) - min(b[0] for b, _ in p.rects)
    out = []
    for b, f in p.rects:
        if (b[2] - b[0]) <= 1 or (b[3] - b[1]) <= 1 or not f:
            continue
        rgb = [int(f[i:i + 2], 16) for i in (1, 3, 5)]
        if min(rgb) >= 235 or (b[2] - b[0]) > 0.5 * max(span, 1.0):
            continue
        out.append((b, f))
    return out


def plot_area(p: Prims, fallback) -> tuple[float, float, float, float]:
    """The box of the plotted marks (bars or polylines); `fallback` when there are none."""
    xs, ys = [], []
    for points, _ in p.paths:                    # a line chart's plot is its lines
        xs += [x for x, _ in points]
        ys += [y for _, y in points]
    if not xs:
        for b, _ in _bars(p):
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else tuple(fallback)


def chart_style(p: Prims, plot) -> ChartStyle:
    bars = _bars(p)
    vertical = bool(bars) and np.median([b[3] - b[1] for b, _ in bars]) >= np.median([b[2] - b[0] for b, _ in bars])
    orient = ("line" if p.paths else "none") if not bars else ("vertical" if vertical else "horizontal")
    ratio = 0.0
    if len(bars) >= 2:
        key = 0 if vertical else 1
        ordered = sorted(bars, key=lambda r: r[0][key])
        pitch = np.median(np.diff([(b[key] + b[key + 2]) / 2 for b, _ in ordered]))
        ratio = float(np.median([b[key + 2] - b[key] for b, _ in ordered]) / pitch) if pitch else 0.0
    width = plot[2] - plot[0]
    grid = sum(1 for (x0, y0, x1, y1), w in p.lines if abs(y0 - y1) < 0.5 and abs(x1 - x0) > 0.8 * width and w <= 1)
    axis = any(abs(x0 - x1) < 0.5 for (x0, y0, x1, y1), w in p.lines) or grid > 0
    labels_on_bars = 0
    for t, tb, _ in p.texts:
        if _num(t) is None:
            continue
        for bb, _ in bars:
            if bb[0] - 2 <= (tb[0] + tb[2]) / 2 <= bb[2] + 2 and abs(tb[3] - bb[1]) < 12:
                labels_on_bars += 1
                break
    colors: list[str] = []
    for f in [f for _, f in bars] + [c for _, c in p.paths]:
        if f and f not in colors:
            colors.append(f)
    sizes = [s for _, _, s in p.texts]
    return ChartStyle(orient, len(bars), round(ratio, 2), grid, axis, float(np.median(sizes)) if sizes else None,
                      bool(bars) and labels_on_bars >= max(1, len(bars) // 2), colors)


def style_similarity(a: ChartStyle, b: ChartStyle) -> float:
    """0..1, ignores values: orientation, bar width ratio, gridlines, axis, data labels, tick size, colour count."""
    parts = [a.orientation == b.orientation,
             1 - min(abs(a.bar_width_ratio - b.bar_width_ratio) / max(a.bar_width_ratio, 0.05), 1),
             (a.gridlines > 0) == (b.gridlines > 0), a.has_axis_line == b.has_axis_line,
             a.has_data_labels == b.has_data_labels,
             1.0 if a.tick_font_size is None or b.tick_font_size is None
             else 1 - min(abs(a.tick_font_size - b.tick_font_size) / a.tick_font_size, 1),
             len(a.colors) == len(b.colors)]
    return float(np.mean([float(x) for x in parts]))


def _value_axis(p: Prims, plot):
    """Least-squares value = a*y + b on the numeric tick labels left of the plot; (a, b, residual share)."""
    ticks = [(_num(t), (bb[1] + bb[3]) / 2) for t, bb, _ in p.texts
             if _num(t) is not None and bb[2] <= plot[0] + 2]
    if len(ticks) < 2:
        return None
    v, y = np.array([t[0] for t in ticks]), np.array([t[1] for t in ticks])
    if np.ptp(y) < 1:
        return None
    a, b = np.polyfit(y, v, 1)
    resid = float(np.abs(a * y + b - v).max() / max(np.ptp(v), 1e-9))
    return a, b, resid


def recover_vertical_bars(p: Prims, plot) -> tuple[list[str], list[float], float]:
    """-> (categories, values, confidence). Each bar value = f(bar top) - f(baseline)."""
    bars = sorted(_bars(p), key=lambda r: r[0][0])
    axis = _value_axis(p, plot)
    if len(bars) < 2 or axis is None:
        return [], [], 0.0
    a, b, resid = axis
    base = b + a * max(r[0][3] for r in bars)              # value at the common bottom edge
    values = [float(a * bb[1] + b - base) for bb, _ in bars]
    cats = []
    for bb, _ in bars:
        cx = (bb[0] + bb[2]) / 2
        below = [(t, tb) for t, tb, _ in p.texts if tb[1] >= bb[3] - 1 and tb[0] - 4 <= cx <= tb[2] + 4]
        cats.append(min(below, key=lambda x: x[1][1])[0] if below else f"C{len(cats) + 1}")
    return cats, values, float(max(0.0, 1 - 10 * resid))


def recover_lines(p: Prims, plot) -> tuple[list[str], list[tuple[str, list[float]]], float]:
    """-> (categories, [(series name, values)], confidence) for a line chart: every polyline is read at
    the x position of each x-axis label (the labels under the plot), through the fitted value axis.
    Series names come from the legend words above or beside the plot, in reading order."""
    axis = _value_axis(p, plot)
    if not p.paths or axis is None:
        return [], [], 0.0
    a, b, resid = axis
    below = [(t, tb) for t, tb, _ in p.texts if tb[1] >= plot[3] - 1 and _num(t) is None]
    if below:                                   # the axis labels are the FIRST row under the plot only
        top = min(tb[1] for _, tb in below)
        below = [(t, tb) for t, tb in below if tb[1] <= top + 3 and len(t) <= 16]
    below = sorted(below, key=lambda x: (x[1][0] + x[1][2]) / 2)
    if len(below) < 2:
        return [], [], 0.0
    xs = [(tb[0] + tb[2]) / 2 for _, tb in below]

    def legend_name(colour: str | None) -> str | None:
        """The text just right of a legend swatch drawn in the series' colour."""
        for (x0, y0, x1, y1), mark in p.marks:
            if mark != colour:
                continue
            cy = (y0 + y1) / 2
            near = [(tb[0] - x1, t) for t, tb, _ in p.texts if _num(t) is None
                    and -2 <= tb[0] - x1 <= 20 and tb[1] - 3 <= cy <= tb[3] + 3]
            if near:
                return min(near)[1]
        return None

    series = []
    for k, (points, colour) in enumerate(p.paths):
        pts = sorted(points)
        px, py = np.array([q[0] for q in pts]), np.array([q[1] for q in pts])
        inside = [x for x in xs if px.min() - 2 <= x <= px.max() + 2]
        if len(inside) < len(xs):                     # the line does not span every label: not trusted
            return [], [], 0.0
        values = [float(a * np.interp(x, px, py) + b) for x in xs]
        series.append((legend_name(colour) or f"Series {k + 1}", values))
    return [t for t, _ in below], series, float(max(0.0, 1 - 10 * resid))


def cross_check_with_labels(p: Prims, values: list[float], rel_tol: float = 0.02) -> float | None:
    """Share of printed data labels that agree with recovered values (None when the chart has no labels)."""
    labels = [_num(t) for t, _, _ in p.texts if _num(t) is not None]
    if not labels:
        return None
    hits = sum(any(abs(l - v) <= rel_tol * max(abs(v), 1e-9) for l in labels) for v in values)
    return hits / len(values) if values else None


def recover_chart(page, figure_id: str, bbox, min_conf: float = 0.9):
    """F-07: a lone vector chart's data from its geometry, as a RecoveredChart, or None (then the VLM)."""
    from src.contracts.report import RecoveredChart, RecoveredSeries
    p = primitives_from_page(page, bbox)
    plot = plot_area(p, bbox)
    if _bars(p):
        cats, values, conf = recover_vertical_bars(p, plot)
        check = cross_check_with_labels(p, values)
        if conf >= min_conf and cats and (check is None or check >= 0.9):
            return RecoveredChart(figure_id=figure_id, categories=cats, confidence=round(conf, 3),
                                  series=[RecoveredSeries(name="Value", values=values)])
        return None
    cats, series, conf = recover_lines(p, plot)
    if conf >= min_conf and cats and series:
        return RecoveredChart(figure_id=figure_id, categories=cats, confidence=round(conf, 3),
                              series=[RecoveredSeries(name=name, values=values) for name, values in series])
    return None
