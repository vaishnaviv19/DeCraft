# src/style/_templates.py
"""Page Templates (M8.2): positioned Blocks, mirror/flow mode and section ownership.

A page is mirrored when every block can be classified and nothing overlaps; otherwise it flows in
reading order with the same styles (E-15). Block kinds come from the text styles and from the page's
drawings, images and tables; no domain assumption is made (E-25).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

from src.config import Config
from src.contracts import SourceDocument
from src.contracts.report import Block, PageTemplate, StyleProfile, TextStyle

PAGE_NUMBER_RE = re.compile(r"^[\s\-–—(]*\d{1,4}[\s\-–—).]*$")
ROW_GAP_EM = 1.5          # a gap wider than this many font sizes between spans on one line = a table cell break


def numeric_share(text: str) -> float:
    """Share of the words that are numbers or period labels ('32,703', '(6,277)', 'FY21A', 'Q1FY24')."""
    from src.core.periods import parse_period
    from src.core.values import parse_number
    words = text.split()
    if not words:
        return 0.0
    return sum(parse_number(w) is not None or parse_period(w) is not None for w in words) / len(words)


def row_like(block) -> bool:
    """A table row or header row laid out as cells: three or more spans on one line with wide gaps
    ('Particulars   Methodology   Multiple   %'). Borderless tables have no drawn box to hide them."""
    spans = [s for line in block.get("lines", []) for s in line.get("spans", []) if s.get("text", "").strip()]
    rows: dict[int, list] = {}
    for span in spans:
        rows.setdefault(round(float(span["bbox"][1]) / 2), []).append(span)
    for cells in rows.values():
        cells.sort(key=lambda s: float(s["bbox"][0]))
        gaps = sum(1 for a, b in zip(cells, cells[1:])
                   if float(b["bbox"][0]) - float(a["bbox"][2]) > ROW_GAP_EM * float(a.get("size", 10.0)))
        if gaps >= 2:
            return True
    return False


CHART_PATH_ITEMS = 6           # a drawing with more pieces than this is a chart line, not a rule or box
CHART_MARGIN = 28.0            # axis labels sit within this distance (pt) of the plotted lines


def chart_areas(page) -> list[tuple[float, float, float, float]]:
    """Boxes of vector chart lines on the page (paths with many small segments), grown by the margin
    where axis labels sit."""
    areas = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return areas
    for drawing in drawings:
        if len(drawing.get("items", [])) > CHART_PATH_ITEMS:
            r = drawing["rect"]
            areas.append((r.x0 - CHART_MARGIN, r.y0 - CHART_MARGIN, r.x1 + CHART_MARGIN, r.y1 + CHART_MARGIN))
    return areas


CHART_MIN_W, CHART_MIN_H = 60.0, 30.0     # smaller multi-segment paths are icons and arrows, not charts


def _merge_boxes(boxes: list[list[float]], gap: float = 6.0) -> list[list[float]]:
    merged: list[list[float]] = []
    for box in sorted(boxes, key=lambda b: (b[1], b[0])):
        for other in merged:
            if box[0] <= other[2] + gap and other[0] <= box[2] + gap \
                    and box[1] <= other[3] + gap and other[1] <= box[3] + gap:
                other[:] = [min(box[0], other[0]), min(box[1], other[1]),
                            max(box[2], other[2]), max(box[3], other[3])]
                break
        else:
            merged.append(list(box))
    return merged


def chart_blocks(page) -> list[Block]:
    """Source charts drawn as vectors (M8.2.1: 'drawing clusters with axis-like lines or many filled
    rectangles'), as `chart` Blocks without axis labels (those are added by `grow_charts`):

    * line charts: paths of lines/curves with more than CHART_PATH_ITEMS pieces, at least
      CHART_MIN_W x CHART_MIN_H; the box is taken from the points that lie ON the page (a price line
      can run past the edge under a clip path);
    * bar charts: at least 4 filled rectangles of similar width that stand on one baseline (or start
      at one left edge) with different lengths. Table cell shading has equal heights, so it is not
      taken for bars.
    Before this, no chart block was ever made, so no chart reached the output (live run 2026-10-26)."""
    width, height = float(page.rect.width), float(page.rect.height)
    try:
        drawings = page.get_drawings(extended=True)       # with the clip paths, to see what is visible
    except Exception:
        return []
    lines: list[list[float]] = []
    fills: list[tuple[float, float, float, float]] = []
    clips: dict[int, tuple[float, float, float, float]] = {}
    for drawing in drawings:
        level = int(drawing.get("level", 0) or 0)
        if drawing.get("type") == "clip":
            scissor = drawing.get("scissor")
            if scissor is not None:
                clips = {k: v for k, v in clips.items() if k < level}
                clips[level] = (float(scissor.x0), float(scissor.y0), float(scissor.x1), float(scissor.y1))
            continue
        if drawing.get("type") == "group" or "items" not in drawing:
            continue
        x0, y0, x1, y1 = 0.0, 0.0, width, height          # the visible area: page AND every open clip
        for k, (cx0, cy0, cx1, cy1) in clips.items():
            if k < level:
                x0, y0, x1, y1 = max(x0, cx0), max(y0, cy0), min(x1, cx1), min(y1, cy1)
        items = drawing.get("items", [])
        kinds = {item[0] for item in items}
        sloped = any(it[0] == "l" and abs(it[1].x - it[2].x) > 0.5 and abs(it[1].y - it[2].y) > 0.5
                     for it in items)
        # a plotted line: many pieces, or a few SLOPED pieces (a short series); the size test below
        # keeps arrows and icons out
        if kinds <= {"l", "c"} and (len(items) > CHART_PATH_ITEMS or (len(items) >= 3 and sloped)):
            points = [point for item in items for point in item[1:] if hasattr(point, "x")
                      and x0 <= point.x <= x1 and y0 <= point.y <= y1]
            if points:
                box = [min(p.x for p in points), min(p.y for p in points),
                       max(p.x for p in points), max(p.y for p in points)]
                if box[2] - box[0] >= CHART_MIN_W and box[3] - box[1] >= CHART_MIN_H:
                    lines.append(box)
        elif drawing.get("fill") and len(items) == 1 and items[0][0] == "re":
            r = items[0][1]
            if 1.5 < r.width < 0.08 * width and r.height > 1.5:
                fills.append((float(r.x0), float(r.y0), float(r.x1), float(r.y1)))
    boxes = _merge_boxes(lines)
    by_base: dict[int, list] = defaultdict(list)          # vertical bars standing on one baseline
    for bar in fills:
        by_base[round(bar[3])].append(bar)
    for group in by_base.values():
        heights = [b[3] - b[1] for b in group]
        widths = [b[2] - b[0] for b in group]
        if len(group) >= 4 and max(heights) - min(heights) > 3.0 and max(widths) <= 1.5 * min(widths) \
                and len({round(b[0]) for b in group}) == len(group):
            boxes.append([min(b[0] for b in group), min(b[1] for b in group),
                          max(b[2] for b in group), max(b[3] for b in group)])
    return [Block(block_id="", kind="chart", bbox=tuple(box)) for box in _merge_boxes(boxes)]


def grow_charts(blocks: list[Block], margin: float = CHART_MARGIN) -> None:
    """A chart's box takes in its axis labels and legend: the label pieces (classified `decoration`
    because they sit beside the plotted lines) within `margin` of it."""
    for chart in [b for b in blocks if b.kind == "chart"]:
        x0, y0, x1, y1 = chart.bbox
        for block in blocks:                   # a tick label taken for a running header ('20') is the chart's
            if block.kind in {"header", "footer", "page_number"} and block.text is not None                     and _inside(block.bbox, (x0 - margin, y0 - margin, x1 + margin, y1 + margin))                     and len(block.text.split()) <= 2:
                block.kind = "decoration"
            if block.kind != "decoration" or block.text is None:
                continue
            bx0, by0, bx1, by1 = block.bbox
            if bx0 >= x0 - margin and bx1 <= x1 + margin and by0 >= y0 - margin and by1 <= y1 + margin:
                x0, y0, x1, y1 = min(x0, bx0), min(y0, by0), max(x1, bx1), max(y1, by1)
        chart.bbox = (x0, y0, x1, y1)


def trim_tables_off_charts(blocks: list[Block]) -> None:
    """A drawn-grid table box is the union of ALL ruling lines on the page, so a chart's axis lines
    stretched it across the chart (sample page 4: the ratings table covered the price chart). A table
    box that overlaps a chart is cut back to the side of the chart it mostly lies on."""
    charts = [b for b in blocks if b.kind == "chart"]
    for table in [b for b in blocks if b.kind == "table"]:
        x0, y0, x1, y1 = table.bbox
        for chart in charts:
            cx0, cy0, cx1, cy1 = chart.bbox
            if cx1 <= x0 or cx0 >= x1 or cy1 <= y0 or cy0 >= y1:
                continue
            right, left = x1 - cx1, cx0 - x0
            below, above = y1 - cy1, cy0 - y0
            best = max(right, left, below, above)
            if best <= 0:
                continue
            if best == right:
                x0 = cx1 + 2
            elif best == left:
                x1 = cx0 - 2
            elif best == below:
                y0 = cy1 + 2
            else:
                y1 = cy0 - 2
        table.bbox = (x0, y0, x1, y1)


def link_charts(blocks: list[Block], doc: SourceDocument | None, page_no: int) -> None:
    """A source chart shows the table on its page whose labels make up at least half of the chart's
    axis-label words (M8.3.2); otherwise it stays without `table_id` and is a lone chart whose data the
    Figure Analyst recovers into a hidden `rc_<block_id>` table (E-19)."""
    if doc is None:
        return
    tables = [t for t in doc.tables if int(t.page) == page_no]

    def tokens(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z][a-z0-9.%/-]+", (text or "").lower()) if len(w) > 2}

    for chart in [b for b in blocks if b.kind == "chart" and not b.table_id]:
        labels = set()
        for block in blocks:
            if block.kind == "decoration" and block.text and _inside(block.bbox, chart.bbox):
                labels |= tokens(block.text)
        if not labels:
            continue
        best, share = None, 0.0
        for table in tables:
            words = tokens(" ".join(list(table.header) + [str(v) for row in table.raw_cells for v in row]))
            overlap = len(labels & words) / len(labels)
            if overlap > share:
                best, share = table.table_id, overlap
        if best is not None and share >= 0.5:
            chart.table_id = best


def near_chart(bbox, areas) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return any(a[0] <= cx <= a[2] and a[1] <= cy <= a[3] for a in areas)


def merge_table_rows(blocks: list[Block], body_size: float, page_width: float | None = None) -> list[Block]:
    """Nearby table pieces (rows OR single cells: within ~2.5 lines vertically, ~3 font sizes
    horizontally) become one table region with the union box. Pieces on different halves of the page
    are not joined sideways, so two tables side by side stay two tables (live run 2026-10-26: page 3
    gave 69 regions for its 4 tables because its cells were separate boxes)."""
    pieces = [b for b in blocks if b.kind == "table"]
    mid = (page_width or 0) / 2

    def side(b):
        if not page_width or (b.bbox[0] < mid < b.bbox[2]):
            return None                          # spans the middle: may join either side
        return 0 if b.bbox[2] <= mid else 1

    def near(a, b) -> bool:
        v_gap = max(b.bbox[1] - a.bbox[3], a.bbox[1] - b.bbox[3], 0.0)
        h_gap = max(b.bbox[0] - a.bbox[2], a.bbox[0] - b.bbox[2], 0.0)
        if side(a) is not None and side(b) is not None and side(a) != side(b):
            return False
        return v_gap <= 2.5 * body_size and h_gap <= 3.0 * body_size

    parent = list(range(len(pieces)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(pieces)):
        for j in range(i + 1, len(pieces)):
            if near(pieces[i], pieces[j]):
                parent[root(i)] = root(j)
    groups: dict[int, list[Block]] = {}
    for i, piece in enumerate(pieces):
        groups.setdefault(root(i), []).append(piece)
    merged = []
    for members in groups.values():
        members.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
        region = members[0].model_copy()
        region.bbox = (min(b.bbox[0] for b in members), min(b.bbox[1] for b in members),
                       max(b.bbox[2] for b in members), max(b.bbox[3] for b in members))
        region.text = "\n".join(b.text or "" for b in members)
        region.source_words = sum(b.source_words for b in members)
        merged.append(region)
    return [b for b in blocks if b.kind != "table"] + merged


def attach_tables(blocks: list[Block], doc: SourceDocument | None, page_no: int, page_width: float) -> None:
    """Each table region gets the source table it shows: the page's tables in source order matched to
    the regions in reading order (left column top to bottom, then the right). Without this the
    renderer had no table to draw (live run 2026-10-26: page 1 rendered no tables at all)."""
    if doc is None:
        return
    tables = [t for t in doc.tables if int(t.page) == page_no]
    regions = sorted((b for b in blocks if b.kind == "table"),
                     key=lambda b: (0 if b.bbox[0] < page_width / 2 else 1, b.bbox[1]))

    def tokens(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9][a-z0-9.,%()/-]*", text.lower()) if len(w) > 1}

    table_tokens = {t.table_id: tokens(" ".join(list(t.header) + [str(v) for row in t.raw_cells for v in row]))
                    for t in tables}
    # content first: the table whose cells overlap the region's text most (a table drawn elsewhere,
    # e.g. inside a header band, does not shift the others); reading order breaks ties
    pairs = []
    for r_index, region in enumerate(regions):
        words = tokens(region.text or "")
        for t_index, table in enumerate(tables):
            overlap = len(words & table_tokens[table.table_id]) / max(len(words), 1)
            pairs.append((overlap, -abs(r_index - t_index), r_index, table.table_id))
    used_regions, used_tables = set(), set()
    for overlap, _, r_index, table_id in sorted(pairs, reverse=True):
        if overlap < 0.2 or r_index in used_regions or table_id in used_tables:
            continue
        regions[r_index].table_id = table_id
        used_regions.add(r_index)
        used_tables.add(table_id)
    # a table whose rows sit beside a chart was taken for axis labels: text pieces whose words are
    # mostly that table's cells become its region again (live run: the page-4 rating history table)
    for table in tables:
        if table.table_id in used_tables:
            continue
        own = table_tokens[table.table_id]
        members = [b for b in blocks if b.kind == "decoration" and b.text
                   and len(tokens(b.text) & own) / max(len(tokens(b.text)), 1) >= 0.6]
        if len(members) < 2:
            continue
        for b in members:
            blocks.remove(b)
        region = members[0].model_copy()
        region.kind, region.table_id = "table", table.table_id
        region.bbox = (min(b.bbox[0] for b in members), min(b.bbox[1] for b in members),
                       max(b.bbox[2] for b in members), max(b.bbox[3] for b in members))
        region.text = "\n".join(b.text or "" for b in members)
        region.source_words = sum(b.source_words for b in members)
        blocks.append(region)
        used_tables.add(table.table_id)


def can_be_heading(block, text: str) -> bool:
    """Live run 2026-10-26: bold table rows ('Revenue 32,703 42,816 ...') opened 44 sections on 5 pages.
    A heading is mostly words and is not laid out as table cells."""
    return numeric_share(text) <= 0.3 and not row_like(block)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _style_for(span_family: str, bold: bool, italic: bool, size: float, color: str,
               style: StyleProfile) -> TextStyle:
    best, best_key = None, None
    for candidate in style.text_styles:
        candidate_size = candidate.size_pt
        key = (abs(candidate_size - size), 0 if candidate.color == color.upper() else 1)
        if best_key is None or key < best_key:
            best, best_key = candidate, key
    assert best is not None
    return best


def _block_style(block, style: StyleProfile) -> tuple[TextStyle, int, bool, str]:
    """Dominant Text Style of a text block, its character count and whether any span is bold."""
    counts: Counter = Counter()
    bold_chars = 0
    text_parts = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            text = span.get("text", "")
            if not text.strip():
                continue
            text_parts.append(text)
            family = str(span.get("font", ""))
            size = float(span.get("size", 10.0))
            color = f"#{int(span.get('color', 0)):06X}"
            flags = int(span.get("flags", 0))
            bold = bool(flags & 16) or "bold" in family.lower()
            ts = _style_for(family, bold, bool(flags & 2), size, color, style)
            counts[ts.style_id] += len(text)
            if bold:
                bold_chars += len(text)
    style_id = counts.most_common(1)[0][0] if counts else style.text_styles[0].style_id
    chosen = next(t for t in style.text_styles if t.style_id == style_id)
    return chosen, sum(counts.values()), bold_chars > 0, " ".join(text_parts)


def _drawing_blocks(page) -> list[Block]:
    """Tables (line grids) and decorations from the vector drawings."""
    horizontal, vertical = [], []
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) < 0.6 and abs(p1.x - p2.x) > 8:
                    horizontal.append((min(p1.x, p2.x), float(p1.y), max(p1.x, p2.x)))
                elif abs(p1.x - p2.x) < 0.6 and abs(p1.y - p2.y) > 8:
                    vertical.append((float(p1.x), min(p1.y, p2.y), max(p1.y, p2.y)))
            elif item[0] == "re":
                rect = item[1]
                horizontal.extend([(rect.x0, rect.y0, rect.x1), (rect.x0, rect.y1, rect.x1)])
                vertical.extend([(rect.x0, rect.y0, rect.y1), (rect.x1, rect.y0, rect.y1)])
    blocks: list[Block] = []
    if horizontal and vertical:
        xs = [h[0] for h in horizontal] + [h[2] for h in horizontal] + [v[0] for v in vertical]
        ys = [h[1] for h in horizontal] + [v[1] for v in vertical] + [v[2] for v in vertical]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        area = max((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]), 1.0)
        if len(horizontal) >= 2 and len(vertical) >= 2 and area < 0.6 * page.rect.width * page.rect.height:
            blocks.append(Block(block_id="", kind="table", bbox=bbox))
        else:
            blocks.append(Block(block_id="", kind="decoration", bbox=bbox))
    elif horizontal or vertical:
        xs = [s[0] for s in horizontal] + [s[2] for s in horizontal] + [v[0] for v in vertical]
        ys = [s[1] for s in horizontal] + [v[1] for v in vertical] + [v[2] for v in vertical]
        blocks.append(Block(block_id="", kind="decoration",
                            bbox=(min(xs), min(ys), max(xs), max(ys))))
    return blocks


def _image_blocks(page, page_no: int, seen_xrefs: Counter) -> list[Block]:
    blocks = []
    placed: set[tuple] = set()               # a PDF that shares image resources across pages lists the
    for image in page.get_images(full=True):   # same picture several times at one place: count it once
        xref = int(image[0])
        seen_xrefs[xref] += 1
        for rect in page.get_image_rects(xref):
            key = tuple(round(v) for v in rect)
            if key in placed:
                continue
            placed.add(key)
            width = rect.width
            height = float(page.rect.height)
            # brand furniture: an image repeated across pages, a small first-page image, or an image in
            # the header/footer band (sample pages 2-5: the banner logo was taken for a photo, so with no
            # generated photo the branding vanished)
            in_band = rect.y1 <= 0.15 * height or rect.y0 >= 0.85 * height
            logo = seen_xrefs[xref] >= 2 or (page_no == 1 and width <= 0.25 * page.rect.width) or in_band
            blocks.append(Block(block_id="", kind="logo" if logo else "photo",
                                bbox=(rect.x0, rect.y0, rect.x1, rect.y1)))
    return blocks


def _inside(bbox, other, slack: float = 2.0) -> bool:
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return (other[0] - slack <= cx <= other[2] + slack
            and other[1] - slack <= cy <= other[3] + slack)


def _overlaps(a, b, minimum: float = 0.1) -> bool:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return False
    inter = width * height
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / max(union, 1e-9) > minimum


def build_templates(pdf_path: Path, doc: SourceDocument, style: StyleProfile,
                    cfg: Config) -> list[PageTemplate]:
    """One Page Template per source page: classified Blocks, mirror/flow mode and sections."""
    body = style.text_styles[0]
    body_size = body.size_pt
    header = _norm(style.header_text or "")
    footer = _norm(style.footer_text or "")
    pages: list[PageTemplate] = []
    seen_xrefs: Counter = Counter()
    section_number = 0
    current_section = "S0"
    section_has_only_headings = False

    with pymupdf.open(str(pdf_path)) as pdf:
        for page_no, page in enumerate(pdf, start=1):
            height = float(page.rect.height)
            drawings = _drawing_blocks(page)
            charts_here = chart_areas(page)
            vector_charts = chart_blocks(page)
            images = _image_blocks(page, page_no, seen_xrefs)
            figure_boxes = [b for b in drawings + images + vector_charts
                            if b.kind in {"table", "photo", "chart"}]

            blocks: list[Block] = []
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                text_style, chars, bold, text = _block_style(block, style)
                text = text.strip()
                if not text or chars == 0:
                    continue
                bbox = tuple(float(v) for v in block["bbox"])
                words = len(text.split())
                compact = _norm(text)

                if any(_inside(bbox, f.bbox) for f in figure_boxes):
                    continue                                  # text inside a table/photo belongs to it
                if PAGE_NUMBER_RE.match(text) and (bbox[1] <= 0.08 * height or bbox[3] >= 0.92 * height):
                    kind = "page_number"
                elif compact and (compact == header or (header and compact in header)):
                    kind = "header"
                elif compact and (compact == footer or (footer and compact in footer)):
                    kind = "footer"
                elif (text_style.size_pt >= 1.2 * body_size or (
                        bold and text_style.size_pt >= body_size and words <= 12)) and can_be_heading(block, text):
                    kind = "heading"
                elif numeric_share(text) > 0.3 or row_like(block):
                    # rows of a borderless table, or a chart's axis labels ('280', 'Sep-22 Dec-22'):
                    # they belong to the table/chart drawn there, never to a writer (live run: 105 of 132
                    # 'text' boxes were such rows, so writers poured prose into table cells)
                    # axis labels beside a vector chart's lines belong to the chart, whatever their length
                    kind = "table" if words >= 4 and not near_chart(bbox, charts_here) else "decoration"
                elif text_style.size_pt < body_size and any(
                        (f.bbox[3] - 4 <= bbox[1] <= f.bbox[3] + 24)
                        or (f.bbox[1] - 24 <= bbox[3] <= f.bbox[1] + 4) for f in figure_boxes):
                    kind = "caption"
                else:
                    kind = "text"
                blocks.append(Block(block_id="", kind=kind, bbox=bbox, text=text,
                                    style_id=text_style.style_id, source_words=words))

            blocks = merge_table_rows(blocks, body_size, float(page.rect.width))
            blocks.extend(drawings)
            attach_tables(blocks, doc, page_no, float(page.rect.width))   # drawn grids too
            blocks.extend(images)
            blocks.extend(vector_charts)
            grow_charts(blocks)
            trim_tables_off_charts(blocks)
            # a table region that is really a chart's labels (inside the chart box) is not a table
            blocks = [b for b in blocks if not (b.kind == "table" and not b.table_id and any(
                _inside(b.bbox, c.bbox) for c in vector_charts))]
            link_charts(blocks, doc, page_no)
            blocks.sort(key=lambda b: (round(b.bbox[1], 1), round(b.bbox[0], 1)))

            # Section ownership: a heading opens a section; front matter is S0. A heading that follows
            # another heading with nothing in between ('BUY', 'Electric Utilities') joins that section
            # instead of opening an empty one.
            for block in blocks:
                if block.kind == "heading" and not (current_section != "S0" and section_has_only_headings):
                    section_number += 1
                    current_section = f"S{section_number}"
                    section_has_only_headings = True
                elif block.kind not in {"heading", "header", "footer", "page_number", "logo"}:
                    section_has_only_headings = False
                block.section_id = current_section
            for index, block in enumerate(blocks, start=1):
                block.block_id = f"p{page_no}b{index:02d}"

            reason = ""
            mode = "mirror"
            if page.rotation:
                mode, reason = "flow", "rotated"
            else:
                # E-48 (owner decision 2026-10-26, overrides E-15): the output keeps the SOURCE layout;
                # only headings, text, table contents and charts change. Pages with many columns or
                # touching boxes are still mirrored (absolute boxes need no reflow); only a rotated
                # page is reflowed. The reason is kept for the record.
                text_blocks = [b for b in blocks if b.kind in {"text", "heading", "caption"}]
                if _column_count(text_blocks) > 3:
                    reason = "columns (mirrored, E-48)"
                elif any(_overlaps(a.bbox, b.bbox) for i, a in enumerate(text_blocks)
                         for b in text_blocks[i + 1:]):
                    reason = "overlap (mirrored, E-48)"
            pages.append(PageTemplate(page=page_no, mode=mode, reason=reason, blocks=blocks))
    return pages


def _column_count(text_blocks: list[Block]) -> int:
    """Distinct text columns: x0 clusters whose blocks sit side by side (vertical overlap)."""
    clusters: dict[int, list[Block]] = {}
    for block in text_blocks:
        clusters.setdefault(round(block.bbox[0] / 15), []).append(block)
    groups = list(clusters.values())
    if len(groups) <= 3:
        return len(groups)
    side_by_side: set[int] = set()
    for i, first in enumerate(groups):
        for j in range(i + 1, len(groups)):
            for a in first:
                for b in groups[j]:
                    vertical = min(a.bbox[3], b.bbox[3]) - max(a.bbox[1], b.bbox[1])
                    disjoint = a.bbox[2] <= b.bbox[0] or b.bbox[2] <= a.bbox[0]
                    if vertical > 5 and disjoint:
                        side_by_side.update({i, j})
    return len(side_by_side)
