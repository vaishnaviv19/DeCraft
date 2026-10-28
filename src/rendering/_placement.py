# src/rendering/_placement.py  -- M8.5.2 / E-53: every displayed table gets its chart
"""Where each table's chart goes.

* a source chart block already showing the table (`chart.table_id == table`) is used as it is;
* flow page: a chart block directly under the table (the flow order puts a table's chart after it);
* mirror page (E-53, replaces the 60/40 split): the table keeps its EXACT source box and every row.
  Its chart goes on a chart page added after the source pages. The page copies the masthead, logo and
  footer of a body page of the source, and holds the charts in a grid in table order.
  Live run 2026-10-26: the split cut the P&L to 14 of its 22 rows and squeezed every table on
  pages 2-3 into 60% of its width, so no page looked like its source.
"""
from __future__ import annotations

import math

from src.contracts.report import Block, PageTemplate, StyleProfile

FLOW_CHART_HEIGHT = (110.0, 190.0)
ROW_PADDING_PT = 2.0
SAFETY = 0.93
CHART_COLUMNS = 2
CHART_ROWS = 3
GAP = 18.0
FURNITURE = {"header", "footer", "logo", "page_number", "decoration"}


def _size(block: Block, style: StyleProfile) -> float:
    return next((t.size_pt for t in style.text_styles if t.style_id == block.style_id), 8.0)


def _lines(text, width: float, size: float) -> int:
    chars = len(str(text or "").strip())
    return max(1, math.ceil(chars * 0.5 * size / max(width, 1.0)))


def _furniture_page(templates: list[PageTemplate]) -> PageTemplate:
    """The body page whose masthead and footer an added page copies: the first page after the cover
    that holds a table, else the last page, else the first."""
    body = [p for p in templates if p.page > 1 and any(b.kind == "table" for b in p.blocks)]
    return body[0] if body else (templates[-1] if templates else None)


def _body_area(page: PageTemplate, style: StyleProfile, pdf_path=None) -> tuple[float, float, float, float]:
    """(left, top, right, bottom) between the furniture page's masthead and footer."""
    m_top, m_right, m_bottom, m_left = style.margins_pt
    height = style.page_height_pt
    top, bottom = m_top, height - m_bottom
    if pdf_path:
        from src.rendering._pages import page_bands
        band_top, band_bottom = page_bands(pdf_path, page.page, style.page_width_pt, height)
        top, bottom = max(top, band_top + 14), min(bottom, band_bottom - 14)
    for block in page.blocks:
        if block.kind in {"header", "logo", "page_number"} and block.bbox[3] <= 0.25 * height:
            top = max(top, block.bbox[3] + 14)
        if block.kind in {"footer", "page_number"} and block.bbox[1] >= 0.8 * height:
            bottom = min(bottom, block.bbox[1] - 14)
    left = max(m_left, 30.0)
    right = style.page_width_pt - max(m_right, 30.0)
    return left, top, right, bottom


def place_charts(templates: list[PageTemplate], charted: set[str], frames: dict,
                 style: StyleProfile, pdf_path=None, add_pages: bool = True) -> tuple[list[PageTemplate], dict[str, int]]:
    """Templates with a chart for every table in `charted`. Mirror tables are never cut: the second
    value (row caps) is always empty and kept for the stage's interface."""
    placed: list[PageTemplate] = []
    pending: list[Block] = []                  # tables whose chart goes on an added page
    for page in templates:
        blocks = [block.model_copy() for block in page.blocks]
        shown = {b.table_id for b in blocks if b.kind == "chart" and b.table_id}
        added: list[Block] = []
        for block in blocks:
            if block.kind != "table" or not block.table_id or block.table_id not in charted \
                    or block.table_id in shown:
                continue
            if page.mode == "flow":
                x0, y0, x1, y1 = block.bbox
                height = y1 - y0
                chart_height = min(max(0.45 * height, FLOW_CHART_HEIGHT[0]), FLOW_CHART_HEIGHT[1])
                added.append(Block(block_id=f"{block.block_id}c", kind="chart",
                                   bbox=(x0, y1 + 0.1, x1, y1 + 0.1 + chart_height),
                                   table_id=block.table_id, section_id=block.section_id,
                                   style_id=block.style_id))
            else:
                pending.append(block)
            shown.add(block.table_id)
        placed.append(page.model_copy(update={"blocks": blocks + added}))
    if pending and placed and add_pages:              # E-58: off -> exactly the source's pages
        placed += _chart_pages(pending, placed, style, pdf_path)
    return placed, {}


def _chart_pages(tables: list[Block], placed: list[PageTemplate], style: StyleProfile,
                 pdf_path=None) -> list[PageTemplate]:
    furniture = _furniture_page(placed)
    left, top, right, bottom = _body_area(furniture, style, pdf_path)
    width = (right - left - GAP * (CHART_COLUMNS - 1)) / CHART_COLUMNS
    height = (bottom - top - GAP * (CHART_ROWS - 1)) / CHART_ROWS
    per_page = CHART_COLUMNS * CHART_ROWS
    pages = []
    next_number = max(p.page for p in placed) + 1
    for start in range(0, len(tables), per_page):
        number = next_number + len(pages)
        blocks = [block.model_copy(update={"block_id": f"p{number}f{i:02d}~{block.block_id}"})
                  for i, block in enumerate(furniture.blocks) if block.kind in FURNITURE]
        for k, table in enumerate(tables[start:start + per_page]):
            row, column = divmod(k, CHART_COLUMNS)
            x0 = left + column * (width + GAP)
            y0 = top + row * (height + GAP)
            blocks.append(Block(block_id=f"p{number}c{k + 1:02d}", kind="chart",
                                bbox=(x0, y0, x0 + width, y0 + height), table_id=table.table_id,
                                section_id=table.section_id, style_id=table.style_id))
        pages.append(PageTemplate(page=number, mode="mirror",
                                  reason=f"chart page (E-53, furniture of page {furniture.page})",
                                  blocks=blocks, source_page=furniture.page))
    return pages
