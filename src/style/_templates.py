# src/style/_templates.py
"""Page Templates (M8.2): positioned Blocks, mirror/flow mode and section ownership.

A page is mirrored when every block can be classified and nothing overlaps; otherwise it flows in
reading order with the same styles (E-15). Block kinds come from the text styles and from the page's
drawings, images and tables; no domain assumption is made (E-25).
"""
from __future__ import annotations

import re
from collections import Counter
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
    for image in page.get_images(full=True):
        xref = int(image[0])
        seen_xrefs[xref] += 1
        for rect in page.get_image_rects(xref):
            width = rect.width
            logo = seen_xrefs[xref] >= 2 or (page_no == 1 and width <= 0.25 * page.rect.width)
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
            images = _image_blocks(page, page_no, seen_xrefs)
            figure_boxes = [b for b in drawings + images if b.kind in {"table", "photo", "chart"}]

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
                elif text_style.size_pt < body_size and any(
                        (f.bbox[3] - 4 <= bbox[1] <= f.bbox[3] + 24)
                        or (f.bbox[1] - 24 <= bbox[3] <= f.bbox[1] + 4) for f in figure_boxes):
                    kind = "caption"
                else:
                    kind = "text"
                blocks.append(Block(block_id="", kind=kind, bbox=bbox, text=text,
                                    style_id=text_style.style_id, source_words=words))

            blocks.extend(drawings)
            blocks.extend(images)
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
                text_blocks = [b for b in blocks if b.kind in {"text", "heading", "caption"}]
                if _column_count(text_blocks) > 3:
                    mode, reason = "flow", "columns"
                elif any(_overlaps(a.bbox, b.bbox) for i, a in enumerate(text_blocks)
                         for b in text_blocks[i + 1:]):
                    mode, reason = "flow", "overlap"
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
