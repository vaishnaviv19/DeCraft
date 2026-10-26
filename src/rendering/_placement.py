# src/rendering/_placement.py  -- M8.5.2: every displayed table gets its chart ON THE PAGE
"""Where each table's chart goes (spec M8.5.2):

* a source chart block already showing the table (`chart.table_id == table`) is used as it is;
* flow page: a chart block directly under the table (the flow order puts a table's chart after it);
* mirror page: the table's box is split 60/40 table/chart - side by side when the box is wide,
  stacked when it is tall - and the table keeps the rows that fit its part (E-17: font sizes never
  change; rows are capped to what fits). The chart is drawn from the capped table, so every plotted
  value is a printed cell (task 7).
"""
from __future__ import annotations

import math

from src.contracts.report import Block, PageTemplate, StyleProfile

SPLIT = 0.6                      # the table's share of its box on a mirror page
WIDE = 1.4                       # width / height above which the split is side by side
ROW_PADDING_PT = 2.0             # top + bottom cell padding (CSS: 1 pt each) and the border
FLOW_CHART_HEIGHT = (110.0, 190.0)


SAFETY = 0.93                    # the estimate leaves this share of the box for rows


def _size(block: Block, style: StyleProfile) -> float:
    return next((t.size_pt for t in style.text_styles if t.style_id == block.style_id), 8.0)


def _lines(text, width: float, size: float) -> int:
    """Lines a cell needs: about half an em per character, at least one."""
    chars = len(str(text or "").strip())
    return max(1, math.ceil(chars * 0.5 * size / max(width, 1.0)))


def rows_that_fit(block: Block, height: float, style: StyleProfile, frame=None) -> int:
    """Data rows a table of this block's Text Style fits in `height` pt (E-17: the font size never
    changes). A label cell longer than its column wraps, so each row counts its wrapped lines; the
    label column is taken as 40% of the table's width (it is the widest)."""
    size = _size(block, style)
    line = size * 1.15
    if frame is None:
        return max(1, math.floor(height * SAFETY / (line + ROW_PADDING_PT)) - 1)
    width = block.bbox[2] - block.bbox[0]
    columns = max(len(frame.columns), 1)
    label_width = 0.4 * width if columns > 1 else width
    other_width = 0.6 * width / max(columns - 1, 1)
    used = max(_lines(c, other_width, size) for c in frame.columns) * line + ROW_PADDING_PT  # header
    rows = 0
    for _, row in frame.iterrows():
        cells = list(row.values)
        lines = max([_lines(cells[0], label_width, size)] + [_lines(v, other_width, size) for v in cells[1:]])
        used += lines * line + ROW_PADDING_PT
        if used > height * SAFETY:
            break
        rows += 1
    return max(1, rows)


def place_charts(templates: list[PageTemplate], charted: set[str], frames: dict,
                 style: StyleProfile) -> tuple[list[PageTemplate], dict[str, int]]:
    """Templates with a chart block for every table in `charted`, and the row cap per table
    ({table_id: rows kept}) for tables whose split box holds fewer rows than the table has."""
    placed: list[PageTemplate] = []
    caps: dict[str, int] = {}
    for page in templates:
        blocks = [block.model_copy() for block in page.blocks]
        shown = {b.table_id for b in blocks if b.kind == "chart" and b.table_id}
        added: list[Block] = []
        for block in blocks:
            if block.kind != "table" or not block.table_id or block.table_id not in charted \
                    or block.table_id in shown:
                continue
            x0, y0, x1, y1 = block.bbox
            width, height = x1 - x0, y1 - y0
            chart_id = f"{block.block_id}c"
            if page.mode == "flow":
                chart_height = min(max(0.45 * height, FLOW_CHART_HEIGHT[0]), FLOW_CHART_HEIGHT[1])
                chart_box = (x0, y1 + 0.1, x1, y1 + 0.1 + chart_height)
            elif width >= WIDE * height:
                split = x0 + SPLIT * width
                block.bbox = (x0, y0, split - 4.0, y1)
                chart_box = (split + 4.0, y0, x1, y1)
            else:
                split = y0 + SPLIT * height
                block.bbox = (x0, y0, x1, split - 3.0)
                chart_box = (x0, split + 3.0, x1, y1)
            if page.mode != "flow":              # the table's part of a split box: rows that fit
                frame = frames.get(block.table_id)
                fit = rows_that_fit(block, block.bbox[3] - block.bbox[1], style, frame)
                if frame is not None and fit < len(frame):
                    caps[block.table_id] = min(fit, caps.get(block.table_id, fit))
            added.append(Block(block_id=chart_id, kind="chart", bbox=chart_box, table_id=block.table_id,
                               section_id=block.section_id, style_id=block.style_id))
            shown.add(block.table_id)
        placed.append(page.model_copy(update={"blocks": blocks + added}))
    return placed, caps
