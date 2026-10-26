# tests/style/test_v33_extraction.py  -- v3.3 F-06 chart style, F-07 exact recovery of vector charts
import pymupdf

from src.style._chart_geometry import (chart_style, cross_check_with_labels, primitives_from_page,
                                       recover_chart, recover_lines, recover_vertical_bars, style_similarity)

VALUES = [120.0, 85.0, 150.0, 60.0]
CATS = ["North", "South", "East", "West"]


def bar_chart_pdf(values=VALUES, labels=True, bar_w=30, color=(0.12, 0.22, 0.39)):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    x0, y_base, scale = 80, 250, 1.2                         # 1 value unit = 1.2 pt
    for v in (0, 50, 100, 150):                              # ticks and gridlines
        y = y_base - v * scale
        page.insert_text((40, y + 3), str(v), fontsize=8)
        page.draw_line((x0, y), (360, y), color=(0.8, 0.8, 0.8), width=0.5)
    for i, (v, c) in enumerate(zip(values, CATS)):
        left = x0 + 20 + i * 70
        page.draw_rect(pymupdf.Rect(left, y_base - v * scale, left + bar_w, y_base), color=None, fill=color)
        page.insert_text((left, y_base + 12), c, fontsize=8)
        if labels:
            page.insert_text((left + 5, y_base - v * scale - 3), f"{v:.0f}", fontsize=7)
    return doc, page


def test_vector_bar_values_recovered_within_1pct():
    doc, page = bar_chart_pdf()
    p = primitives_from_page(page, (30, 40, 370, 270))
    cats, vals, conf = recover_vertical_bars(p, (80, 40, 360, 250))
    assert cats == CATS and conf > 0.9
    assert all(abs(a - b) / b < 0.01 for a, b in zip(vals, VALUES))
    assert cross_check_with_labels(p, vals) == 1.0
    chart = recover_chart(page, "f1", (30, 40, 370, 270))
    assert chart is not None and chart.categories == CATS


def test_chart_style_extracted_and_compared():
    doc, page = bar_chart_pdf()
    s = chart_style(primitives_from_page(page, (30, 40, 370, 270)), (80, 40, 360, 250))
    assert s.orientation == "vertical" and s.n_bars == 4 and s.gridlines >= 3 and s.has_data_labels
    assert 0.35 <= s.bar_width_ratio <= 0.5
    doc2, page2 = bar_chart_pdf(values=[90, 140, 70, 110], labels=False, bar_w=60)
    s2 = chart_style(primitives_from_page(page2, (30, 40, 370, 270)), (80, 40, 360, 250))
    assert style_similarity(s, s) == 1.0 and style_similarity(s, s2) < 0.85


def test_line_chart_values_and_legend_names_recovered():
    """The sample's lone charts are price lines: each polyline is read at every x-axis label."""
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    y_of = lambda v: 250 - (v - 100) * 1.0                    # 1 value unit = 1 pt above 100
    for v in (100, 150, 200):
        page.insert_text((40, y_of(v) + 3), str(v), fontsize=8)
    months = ["Jan-24", "Apr-24", "Jul-24", "Oct-24"]
    xs = [100, 180, 260, 340]
    for x, m in zip(xs, months):
        page.insert_text((x - 12, 265), m, fontsize=8)
    truth = {"Alpha": [120, 170, 140, 190], "Beta": [110, 115, 160, 150]}
    for (name, vals), colour, ly in zip(truth.items(), [(0, 0.5, 0.5), (0.3, 0.5, 0.8)], [40, 52]):
        shape = page.new_shape()                               # 12 segments through the four points
        points = []
        for (xa, va), (xb, vb) in zip(zip(xs, vals), zip(xs[1:], vals[1:])):
            for k in range(4):
                points.append((xa + (xb - xa) * k / 4, y_of(va + (vb - va) * k / 4)))
        points.append((xs[-1], y_of(vals[-1])))
        shape.draw_polyline(points)
        shape.finish(color=colour, width=1)
        shape.commit()
        page.draw_line((120, ly), (140, ly), color=colour, width=1.5)        # legend swatch
        page.insert_text((144, ly + 3), name, fontsize=8)
    chart = recover_chart(page, "f2", (30, 30, 370, 270))
    assert chart is not None and chart.categories == months and chart.confidence >= 0.9
    got = {s.name: s.values for s in chart.series}
    for name, vals in truth.items():
        assert all(abs(a - b) <= 0.01 * b for a, b in zip(got[name], vals)), (name, got[name])
