# src/rendering/_table_look.py  -- E-54: every table drawn on its source grid, cell by cell
"""The look of each source table, read from the source PDF on docling's cell grid.

Docling gives every table cell (row, column, spans, text box). Under each cell the source page has
the fill that colours it and the text spans that say how it is printed. From these, per table:
column widths and row heights (the cell boxes), per cell the fill, text colour, bold / italic, font,
alignment and padding, and the gap between filled cells (the white gridlines). The renderer rebuilds
the table on exactly that grid, so the output table sits where the source table sat and looks like it.
Numbers are printed in their SOURCE cell's format (thousands separators, '(13.4)' negatives, '%',
'x', 'bps', 'Rs. ', decimals per cell) and '-' / 'n.m.' cells stay as printed.
"""
from __future__ import annotations

import html as html_lib
import json
import re

import numpy as np
from collections import Counter
from pathlib import Path

from src.core.grid import format_cell
from src.core.missingness import token_kind
from src.core.values import parse_number

MAX_PAD = 8.0


def docling_cells(structured_dir) -> dict[str, list[dict]]:
    """table_id -> docling table cells (TOPLEFT boxes in PDF points), from document_structure.json."""
    path = Path(structured_dir) / "document_structure.json" if structured_dir else None
    if path is None or not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for index, table in enumerate(document.get("tables") or [], start=1):
        cells = (table.get("data") or {}).get("table_cells") or []
        if cells:
            out[f"table_{index:03d}"] = cells
    return out


def _colour(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return "#{:02X}{:02X}{:02X}".format(*(int(round(min(1.0, max(0.0, x)) * 255)) for x in value[:3]))
    if isinstance(value, int):
        return f"#{value:06X}"
    return None


def _bands(intervals: dict[int, list[tuple[float, float]]], count: int) -> list[tuple[float, float]] | None:
    """Per index the [min, max] of its intervals; None when an index has none."""
    out = []
    for i in range(count):
        if not intervals.get(i):
            return None
        out.append((min(a for a, _ in intervals[i]), max(b for _, b in intervals[i])))
    return out


def _edges(bands: list[tuple[float, float]]) -> list[float]:
    """Boundaries between consecutive bands (midpoints of gaps or overlaps) plus the outer edges."""
    edges = [bands[0][0]]
    for (a0, a1), (b0, b1) in zip(bands, bands[1:]):
        edges.append((a1 + b0) / 2)
    edges.append(bands[-1][1])
    return edges


def table_look(page, cells: list[dict], box) -> dict | None:
    """The grid look of one table on a pymupdf page; None when the grid cannot be read."""
    import fitz

    n_rows = max(c["end_row_offset_idx"] for c in cells)
    n_cols = max(c["end_col_offset_idx"] for c in cells)
    if n_rows < 1 or n_cols < 1:
        return None
    area = fitz.Rect(box) + (-12, -12, 12, 12)
    fills = []
    for drawing in page.get_drawings():
        rect = fitz.Rect(drawing["rect"])
        colour = _colour(drawing.get("fill"))
        if colour and rect.width >= 2 and rect.height >= 4 and rect.intersects(area) \
                and rect.width <= area.width and rect.height <= 60:
            fills.append((rect, colour))
    spans = [s for b in page.get_text("dict")["blocks"] if b.get("type") == 0
             for line in b["lines"] for s in line["spans"] if s["text"].strip()]

    def text_box(cell):
        b = cell["bbox"]
        return fitz.Rect(b["l"], b["t"], b["r"], b["b"])

    looks: dict[tuple[int, int], dict] = {}
    xs: dict[int, list] = {}
    ys: dict[int, list] = {}
    for cell in cells:
        r, c = cell["start_row_offset_idx"], cell["start_col_offset_idx"]
        rspan = cell["end_row_offset_idx"] - r
        cspan = cell["end_col_offset_idx"] - c
        tb = text_box(cell)
        centre = fitz.Point((tb.x0 + tb.x1) / 2, (tb.y0 + tb.y1) / 2)
        under = [(rect, colour) for rect, colour in fills if rect.contains(centre)
                 and rect.height <= max(3.5 * max(tb.height, 6.0), 14.0)]
        cell_box = tb
        fill = None
        if under:
            biggest = max(under, key=lambda item: item[0].get_area())
            cell_box = biggest[0]
            fill = under[-1][1]                       # the last drawn is on top
        inside = [s for s in spans if fitz.Rect(s["bbox"]).intersects(tb + (-1, -1, 1, 1))
                  and tb.contains(fitz.Point((s["bbox"][0] + s["bbox"][2]) / 2, (s["bbox"][1] + s["bbox"][3]) / 2))]
        weight = Counter()
        for s in inside:
            weight[(s["font"], bool(s["flags"] & 16) or "bold" in s["font"].lower(),
                    bool(s["flags"] & 2) or "italic" in s["font"].lower() or "oblique" in s["font"].lower(),
                    s["color"], round(s["size"], 1))] += len(s["text"].strip())
        font, bold, italic, colour, size = (weight.most_common(1)[0][0] if weight
                                            else (None, False, False, 0, None))
        looks[(r, c)] = {"rspan": rspan, "cspan": cspan, "fill": fill, "font": font, "bold": bold,
                         "italic": italic, "color": f"#{colour:06X}", "size": size,
                         "text": tb if inside else None, "box": cell_box}
        if cspan == 1:
            xs.setdefault(c, []).append((cell_box.x0, cell_box.x1))
        if rspan == 1:
            ys.setdefault(r, []).append((cell_box.y0, cell_box.y1))
    # column widths come from the rows that use every column on its own: a stacked fact sheet mixes
    # two-column rows (label | value) with a full grid, and the wide value cells would swallow columns
    singles = Counter(cell["start_row_offset_idx"] for cell in cells
                      if cell["end_col_offset_idx"] - cell["start_col_offset_idx"] == 1)
    full = {r for r, count in singles.items() if count == max(singles.values())} if singles else set()
    if full:
        xs_full: dict[int, list] = {}
        for cell in cells:
            r, c = cell["start_row_offset_idx"], cell["start_col_offset_idx"]
            if r in full and cell["end_col_offset_idx"] - c == 1:
                box = looks[(r, c)]["box"]
                xs_full.setdefault(c, []).append((box.x0, box.x1))
        if len(xs_full) == n_cols:
            xs = xs_full
    cols, rows = _bands(xs, n_cols), _bands(ys, n_rows)
    if cols is None or rows is None:
        return None
    x_edges, y_edges = _edges(cols), _edges(rows)
    # row edges halfway between the text centres of neighbouring rows: a white row has no fill box,
    # only its text box (7 pt for 8 pt text), so fill-based bands alternated 11 / 7 pt and the 7 pt
    # rows grew - the table drifted 1-2 pt per row (live run 2026-10-27)
    centres = {}
    for cell in cells:
        r = cell["start_row_offset_idx"]
        if cell["end_row_offset_idx"] - r == 1:
            centres.setdefault(r, []).append((cell["bbox"]["t"] + cell["bbox"]["b"]) / 2)
    if len(centres) == n_rows:
        mid = [float(np.median(centres[r])) for r in range(n_rows)]
        inner = [(a + b) / 2 for a, b in zip(mid, mid[1:])]
        y_edges = [min(y_edges[0], mid[0] - (inner[0] - mid[0]) if inner else y_edges[0])] + inner + \
                  [max(y_edges[-1], mid[-1] + (mid[-1] - inner[-1]) if inner else y_edges[-1])]
    if any(b <= a for a, b in zip(x_edges, x_edges[1:])) or any(b <= a for a, b in zip(y_edges, y_edges[1:])):
        return None
    looks = _snap_spans(looks, x_edges, n_cols)
    # a cell whose words the source printed in one span with its neighbour ('Price Performance  3 Month')
    # has no span of its own: it takes its row's style (white bold on the teal band, not black)
    for (r, c), look in looks.items():
        if look["font"] is not None:
            continue
        styled = Counter((o["font"], o["bold"], o["italic"], o["color"], o["size"])
                         for (rr, _), o in looks.items() if rr == r and o["font"] is not None)
        if styled:
            look["font"], look["bold"], look["italic"], look["color"], look["size"] = styled.most_common(1)[0][0]
    # the white gaps between neighbouring filled cells are the source's gridlines
    gaps = [b0 - a1 for (a0, a1), (b0, b1) in zip(cols, cols[1:]) if 0 < b0 - a1 < 3]
    gaps += [b0 - a1 for (a0, a1), (b0, b1) in zip(rows, rows[1:]) if 0 < b0 - a1 < 3]
    gap = round(sorted(gaps)[len(gaps) // 2], 2) if gaps and any(l["fill"] for l in looks.values()) else 0.0
    # alignment: per column for the body (numbers grow and shrink), per cell for the header
    for (r, c), look in looks.items():
        x0, x1 = x_edges[c], x_edges[c + look["cspan"]]
        tb = look["text"]
        if tb is None:
            look.update(align=None, pad_l=2.0, pad_r=2.0)
            continue
        left, right = tb.x0 - x0, x1 - tb.x1
        look["nowrap"] = look["size"] is not None and tb.height < 1.7 * look["size"]   # one source line
        if left + right < 0.25 * (x1 - x0):
            align = None                              # the text fills its cell: the column decides
            words = [s for s in spans if tb.contains(fitz.Point((s["bbox"][0] + s["bbox"][2]) / 2,
                                                                 (s["bbox"][1] + s["bbox"][3]) / 2))]
            text = " ".join(s["text"] for s in words)
            if sum(ch.isalpha() for ch in text) > 0.5 * max(len(text.strip()), 1):
                align = "justify" if len(text) > 60 else "left"     # a long statement wraps like prose
        elif abs(left - right) <= 0.2 * (x1 - x0) and left > 3 and right > 3:
            align = "center"
        elif right < left:
            align = "right"
        else:
            align = "left"
        look.update(align=align, pad_l=min(max(left, 1.0), MAX_PAD), pad_r=min(max(right, 1.0), MAX_PAD))
        if align is None:
            look.update(pad_l=min(max(left, 1.0), 3.0), pad_r=min(max(right, 1.0), 3.0))
    for c in range(n_cols):
        body = Counter(looks[(r, c)]["align"] for r in range(1, n_rows)
                       if (r, c) in looks and looks[(r, c)]["align"] and looks[(r, c)]["cspan"] == 1)
        main = body.most_common(1)[0][0] if body else ("left" if c == 0 else "right")
        for r in range(0, n_rows):
            look = looks.get((r, c))
            if look is None:
                continue
            if r > 0 and look["cspan"] == 1 and look["text"] is None:
                look["align"] = main
            elif look["align"] is None:
                look["align"] = main
    for look in looks.values():
        look.pop("text", None)
        look.pop("box", None)
    return {"x0": x_edges[0], "y0": y_edges[0], "cols": [b - a for a, b in zip(x_edges, x_edges[1:])],
            "rows": [b - a for a, b in zip(y_edges, y_edges[1:])], "cells": looks, "gap": gap,
            "n_rows": n_rows, "n_cols": n_cols}


def _snap_spans(looks: dict, x_edges: list[float], n_cols: int) -> dict:
    """Docling's column spans of a two-column row inside a wider grid are approximate (the value of
    'Market Cap (cr)' was one narrow column, so 'Rs. 69,795' wrapped). A filled cell spans the grid
    columns its FILL covers; a row whose snapped cells would overlap keeps docling's spans."""
    by_row: dict[int, list] = {}
    for (r, c), look in looks.items():
        by_row.setdefault(r, []).append((c, look))
    out = {}
    for r, items in by_row.items():
        snapped = []
        for c, look in items:
            box = look["box"]
            start, span = c, look["cspan"]
            if look["fill"] is not None and look["rspan"] == 1:
                s = min(range(n_cols + 1), key=lambda i: abs(x_edges[i] - box.x0))
                e = min(range(n_cols + 1), key=lambda i: abs(x_edges[i] - box.x1))
                if e > s:
                    start, span = s, e - s
            snapped.append((start, span, look))
        used, ok = set(), True
        for start, span, _ in snapped:
            cols = set(range(start, start + span))
            if cols & used or start + span > n_cols:
                ok = False
                break
            used |= cols
        for (c, look), (start, span, _) in zip(items, snapped):
            if ok:
                out[(r, start)] = {**look, "cspan": span}
            else:
                out[(r, c)] = look
    return out


def grid_looks(pdf_path, templates, cells_by_table: dict[str, list[dict]]) -> dict[str, dict]:
    """block_id -> grid look, for every source table block whose docling grid could be read."""
    out: dict[str, dict] = {}
    if not pdf_path or not cells_by_table:
        return out
    try:
        import fitz
        document = fitz.open(pdf_path)
    except Exception:
        return out
    with document:
        for template in templates:
            if template.source_page or not 1 <= template.page <= document.page_count:
                continue
            page = document[template.page - 1]
            for block in template.blocks:
                if block.kind != "table" or block.table_id not in cells_by_table:
                    continue
                try:
                    look = table_look(page, cells_by_table[block.table_id], block.bbox)
                except Exception:                       # an unreadable grid keeps the plain table
                    look = None
                if look is not None:
                    out[block.block_id] = look
    return out


# ---------------------------------------------------------------- printing values
def _template(raw: list[list[str]], row: int, col: int) -> str | None:
    if 0 <= row < len(raw) and col < len(raw[row]) and parse_number(raw[row][col]) is not None:
        return raw[row][col]
    column = [r[col] for r in raw if col < len(r) and parse_number(r[col]) is not None]
    return Counter(column).most_common(1)[0][0] if column else None


def cell_text(value, raw: list[list[str]] | None, row: int, col: int, style: dict | None) -> str:
    """A display value printed like its source cell."""
    blank = value is None or (isinstance(value, float) and value != value) \
        or str(value).strip().lower() in {"nan", "none", ""}
    source = raw[row][col] if raw and 0 <= row < len(raw) and col < len(raw[row]) else None
    if blank:
        # '-' and 'n.m.' are how the source prints nil / not meaningful: they are layout, not data
        return source.strip() if source and token_kind(source) is not None else ""
    if isinstance(value, str) and source and _date_format(source):
        # the column's style decides the leading zero ('2-Aug-22' in the source: no zero padding)
        column = [r[col] for r in raw if col < len(r) and _date_format(r[col])]
        unpadded = next((t for t in column if re.match(r"\d[^\d]", t.strip())), source)
        return _format_date(value, unpadded) or value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        template = _template(raw or [], row, col)
        if template is not None:
            return format_cell(template, float(value), style)
        number = float(value)
        return f"{number:,.0f}" if number.is_integer() else f"{number:,.1f}"
    return str(value)


def _date_format(text: str) -> str | None:
    from datetime import datetime

    from src.core.values import DATE_FORMATS
    for fmt in DATE_FORMATS:
        try:
            datetime.strptime(str(text).strip(), fmt)
            return fmt
        except ValueError:
            continue
    return None


def _format_date(value: str, template: str) -> str | None:
    """'2022-05-18' printed like the source's '14-May-21' / '2-Aug-22' (no leading zero there)."""
    import pandas as pd
    stamp = pd.to_datetime(value, errors="coerce")
    fmt = _date_format(template)
    if stamp is pd.NaT or stamp is None or fmt is None or pd.isna(stamp):
        return None
    out = stamp.strftime(fmt)
    if re.match(r"\d[^\d]", template.strip()) and out[:1] == "0":
        out = out[1:]
    return out


def heading_text(column) -> str:
    """pandas names blank source headers 'Unnamed: 3'; the source shows nothing there."""
    return "" if re.fullmatch(r"Unnamed: \d+", str(column)) else str(column)


def grid_table_html(frame, look: dict, family: str, size: float, raw, style: dict | None,
                    block_bbox, clean=lambda text: text) -> str | None:
    """The table on its source grid; None when the frame does not have the grid's shape."""
    if look is None or look["n_cols"] != len(frame.columns) or look["n_rows"] != len(frame) + 1:
        return None
    gap = look["gap"]
    family = family.replace("'", '"')                 # the style attribute is single-quoted
    left, top = look["x0"] - block_bbox[0], look["y0"] - block_bbox[1]
    width = sum(look["cols"]) + gap
    css = (f"position:absolute;left:{left - gap / 2:.2f}pt;top:{top - gap / 2:.2f}pt;width:{width:.2f}pt;"
           f"table-layout:fixed;border-collapse:separate;border-spacing:{gap:.2f}pt;"
           f"font-size:{size}pt;{family}line-height:1.1")
    parts = [f"<table class='grid' style='{css}'><colgroup>"]
    parts += [f"<col style='width:{w - gap:.2f}pt'>" for w in look["cols"]]
    parts.append("</colgroup>")
    covered: set[tuple[int, int]] = set()
    values = frame.to_numpy()
    for r in range(look["n_rows"]):
        parts.append(f"<tr style='height:{max(look['rows'][r] - gap, 4.0):.2f}pt'>")
        for c in range(look["n_cols"]):
            if (r, c) in covered:
                continue
            cell = look["cells"].get((r, c), {"rspan": 1, "cspan": 1})
            for rr in range(r, r + cell["rspan"]):
                for cc in range(c, c + cell["cspan"]):
                    covered.add((rr, cc))
            if r == 0:
                text = heading_text(frame.columns[c])
            else:
                text = cell_text(values[r - 1][c], raw, r - 1, c, style)
                if not text and cell["cspan"] > 1:   # a label spanning blank columns
                    text = next((cell_text(values[r - 1][k], raw, r - 1, k, style)
                                 for k in range(c, c + cell["cspan"]) if cell_text(values[r - 1][k], raw, r - 1, k, style)), "")
            styles = [f"padding:0 {cell.get('pad_r', 2.0):.1f}pt 0 {cell.get('pad_l', 2.0):.1f}pt",
                      "vertical-align:middle", "overflow:hidden"]
            if cell.get("fill"):
                styles.append(f"background:{cell['fill']}")
            if cell.get("color"):
                styles.append(f"color:{cell['color']}")
            if cell.get("bold"):
                styles.append("font-weight:bold")
            if cell.get("italic"):
                styles.append("font-style:italic")
            if cell.get("align"):
                styles.append(f"text-align:{cell['align']}")
            if cell.get("nowrap"):
                styles.append("white-space:nowrap")
            if cell.get("font_family"):
                styles.append(cell["font_family"])
            span = (f" colspan='{cell['cspan']}'" if cell["cspan"] > 1 else "") + \
                   (f" rowspan='{cell['rspan']}'" if cell["rspan"] > 1 else "")
            shown = clean(text) if re.search(r"[A-Za-z@]", text) else text     # '-' is not a website
            parts.append(f"<td{span} style='{';'.join(styles)}'>{html_lib.escape(shown)}</td>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)
