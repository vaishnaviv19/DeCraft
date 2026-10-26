# src/rendering/_pages.py
"""Mirror and flow pages (M8.11): one PDF built from the page templates and the fitted block text.

Mirror pages place every block at its source bbox with absolute CSS in points; flow pages put the same
blocks in reading order inside the page margins. Text keeps its Text Style (size never changes), tables
are rebuilt from the display frames with source borders and header fill, charts are inline SVG, photos
and logos are images, and a decoration layer redraws the source drawings behind the blocks."""
from __future__ import annotations

import html as html_lib
import json
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.contracts.report import Block, PageTemplate, StyleProfile, TextStyle

from src.rendering._css import justify_extras  # noqa: E402  (v3.3 F-10)
TEMPLATES = Path(__file__).resolve().parent / "templates"


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))


# websites, e-mail addresses and bare domains (user rule 2026-10-26: no website is ever mentioned)
_WEBSITE = re.compile(
    r"(?:https?://|www\.)[^\s()<>\[\]]*[^\s()<>\[\].,;:!?'\"]"       # http://..., www.... (not its trailing ').')
    r"|\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"                              # e-mail addresses
    r"|\b(?:[a-z0-9-]+\.)+(?:com|net|org|in|co|io|biz|info|gov|edu|uk|us)\b(?:/\S*)?",
    re.I)


def strip_websites(text: str) -> str:
    """Remove every website / e-mail / domain from rendered text, tidying the gap it leaves."""
    if not text:
        return text or ""
    out = _WEBSITE.sub("", str(text))
    out = re.sub(r"\(\s*\)|\[\s*\]", "", out)                         # emptied brackets
    out = re.sub(r"\b(?:web\s?site|web|url|e-?mail|mail|contact|visit(?: us)?(?: at)?)\s*:?\s*(?=[|,;\n]|$)",
                 "", out, flags=re.I | re.M)                          # labels left with nothing after them
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"[ \t]+([,.;:])", r"\1", out)
    return re.sub(r"(?m)^[\s|:,;\-]+$", "", out).strip()


def _paragraphs(text: str) -> str:
    text = strip_websites(text)
    parts =[part.strip() for part in re.split(r"\n\s*\n", text or "") if part.strip()]
    return "".join(f"<p>{html_lib.escape(part)}</p>" for part in parts) or "<p></p>"


def text_style(style: StyleProfile, block: Block) -> TextStyle:
    for item in style.text_styles:
        if item.style_id == block.style_id:
            return item
    return TextStyle(style_id="fallback", font_id="F1", size_pt=10.0, color="#000000",
                     line_height=1.2)


def _font_family(font_id: str) -> str:
    return f"dc-{font_id}"


def css_for(style: StyleProfile, artifacts_root: Path | None,
            adjustments: dict | None = None) -> str:
    adj = adjustments or {}
    parts = ["@page { margin: 0 }", "html, body { margin: 0; padding: 0 }",
             ".sheet { position: relative; overflow: hidden; page-break-after: always; "
             "break-inside: avoid; page-break-inside: avoid }",
             ".sheet:last-child { page-break-after: auto }",
             ".block { position: absolute; overflow: hidden; box-sizing: border-box; "
             "break-inside: avoid; page-break-inside: avoid }",
             f".txt p {{ margin: 0 0 {adj.get('paragraph_spacing', {}).get('*', 0)}pt 0 }}",
             ".sheet.flow { position: relative; overflow: hidden }",
             # blocks are placed by page_models (explicit columns); a column CLIPS what does not fit.
             # 'break-inside: avoid' made WeasyPrint push such a block onto a NEW page (6 pages for 5)
             ".sheet.flow .block { position: static; overflow: visible; margin: 0 0 6pt 0 }",
             ".sheet.flow .flow-body { overflow: hidden; break-inside: avoid; page-break-inside: avoid }",
             ".sheet.flow .block.fixed { position: absolute; overflow: hidden; margin: 0 }",
             ".pic { width: 100%; height: 100%; object-fit: contain }",
             ".pageno { font-size: 8pt; color: #666666 }"]
    for font in style.fonts:
        path = None
        if font.file:
            candidate = Path(font.file)
            for item in ([candidate] if candidate.is_absolute() else []) + \
                    ([Path(artifacts_root) / candidate] if artifacts_root else []):
                if item.exists():
                    path = item.resolve()
                    break
        family = f'"{_font_family(font.font_id)}"' if path else f'"{font.fallback}"'
        if path:
            parts.append(f"@font-face {{ font-family: {family}; src: url('{path.as_uri()}'); }}")
        parts.append(f".font-{font.font_id} {{ font-family: {family}; }}")
    for text in style.text_styles:
        parts.append(
            f".text-{text.style_id} {{ font-family: '{_font_family(text.font_id)}', "
            f"'{_fallback(style, text.font_id)}'; font-size: {text.size_pt}pt; "
            f"color: {text.color}; "
            f"line-height: {adj.get('line_height', {}).get(text.style_id, text.line_height)}; "
            f"text-align: {text.align};{justify_extras(text.align)} }}")      # F-10 hyphenation
    border = style.table_border_pt
    fill = style.table_header_fill or "transparent"
    parts.append(f"table.src {{ border-collapse: collapse; width: 100%; border: {border}pt solid #999; }}")
    parts.append(f"table.src th {{ background: {fill}; font-weight: bold; }}")
    parts.append(f"table.src th, table.src td {{ border: {border}pt solid #999; "
                 f"padding: {adj.get('table_row_padding', {}).get('*', 1.0)}pt "
                 f"{2 * adj.get('table_row_padding', {}).get('*', 1.0)}pt; "
                 f"line-height: 1.15; }}")
    parts.append(".ink-light, .ink-light * { color: #FFFFFF !important }")
    parts.append(".ink-dark, .ink-dark * { color: #000000 !important }")
    parts.append(".deco { position: absolute; left: 0; top: 0; width: 100%; height: 100%; "
                 "pointer-events: none; }")
    return "\n".join(parts)


def _fallback(style: StyleProfile, font_id: str) -> str:
    for font in style.fonts:
        if font.font_id == font_id:
            return font.fallback
    return "sans-serif"


def table_looks(pdf_path, templates: list[PageTemplate]) -> dict[str, dict]:
    """Each source table's own look, read from its page (M8.11.1: 'the source border width, header
    fill and font'): the fill behind its first row and that row's text colour, and the fill behind
    its first column. {block_id: {"header_fill", "header_color", "label_fill"}}; missing keys = none."""
    looks: dict[str, dict] = {}
    try:
        import fitz
        document = fitz.open(pdf_path)
    except Exception:
        return looks
    with document:
        for template in templates:
            if not 1 <= template.page <= document.page_count:
                continue
            page = document[template.page - 1]
            fills = [(fitz.Rect(d["rect"]), _colour(d.get("fill"))) for d in page.get_drawings()
                     if d.get("fill")]
            spans = [span for block in page.get_text("dict").get("blocks", []) if block.get("type") == 0
                     for line in block["lines"] for span in line["spans"] if span["text"].strip()]
            for block in template.blocks:
                if block.kind != "table":
                    continue
                box = fitz.Rect(block.bbox)
                header = fitz.Rect(box.x0, box.y0 - 2, box.x1, box.y0 + 16)
                label = fitz.Rect(box.x0 - 2, box.y0 + 16, box.x0 + 0.35 * box.width, box.y1)
                look: dict[str, str] = {}

                def main_fill(area: "fitz.Rect", share: float) -> str | None:
                    covered: dict[str, float] = {}
                    for rect, colour in fills:
                        part = rect & area
                        if part.is_empty or colour.upper() in {"#FFFFFF", "NONE"}:
                            continue
                        covered[colour] = covered.get(colour, 0.0) + part.get_area()
                    if not covered:
                        return None
                    colour, size = max(covered.items(), key=lambda item: item[1])
                    return colour if size >= share * max(area.get_area(), 1.0) else None

                head_fill = main_fill(header, 0.35)
                if head_fill:
                    look["header_fill"] = head_fill
                    colours = [span.get("color", 0) for span in spans
                               if fitz.Rect(span["bbox"]).intersects(header)]
                    found = f"#{max(set(colours), key=colours.count):06X}" if colours else "#000000"
                    text_l, back_l = _luminance(found), _luminance(head_fill)
                    if (max(text_l, back_l) + 0.05) / (min(text_l, back_l) + 0.05) < 3.0:
                        found = "#FFFFFF" if back_l < 0.4 else "#000000"     # readable on the fill
                    look["header_color"] = found
                label_fill = main_fill(label, 0.35)
                if label_fill and label_fill != head_fill:
                    look["label_fill"] = label_fill
                looks[block.block_id] = look
    return looks


def _table_html(block: Block, tables: dict, style: StyleProfile, font_size: float,
                look: dict | None = None) -> str:
    frame = tables.get(block.table_id or "")
    if frame is None:
        return "<div class='txt'><p></p></div>"
    look = look or {}
    text = text_style(style, block)
    family = f"font-family:'{_font_family(text.font_id)}','{_fallback(style, text.font_id)}';"
    head_css = (f" style='background:{look['header_fill']};color:{look.get('header_color', '#000000')}'"
                if look.get("header_fill") else "")
    label_css = f" style='background:{look['label_fill']}'" if look.get("label_fill") else ""
    def cell(value) -> str:
        """Blank for missing values (CSV 'nan') - the source shows nothing there."""
        if value is None or (isinstance(value, float) and value != value) or str(value).strip().lower() in {"nan", "none"}:
            return ""
        return html_lib.escape(strip_websites(str(value)))

    def heading(column) -> str:
        """pandas names blank source headers 'Unnamed: 3'; the source shows nothing there."""
        return "" if re.fullmatch(r"Unnamed: \d+", str(column)) else html_lib.escape(str(column))

    head = "".join(f"<th{head_css}>{heading(column)}</th>" for column in frame.columns)
    body = "".join("<tr>" + "".join(f"<td{label_css if i == 0 else ''}>{cell(value)}</td>"
                                    for i, value in enumerate(row)) + "</tr>"
                   for row in frame.itertuples(index=False, name=None))
    return (f"<table class='src' style='font-size:{font_size}pt;{family}'><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table>")


def _chart_html(block: Block, charts: dict) -> str:
    # a lone source chart is drawn from its recovered hidden table 'rc_<block_id>' (E-19)
    path = charts.get(block.table_id or "") or charts.get(f"rc_{block.block_id}")
    if not path or not Path(path).exists():
        return ""
    svg = Path(path).read_text(encoding="utf-8")
    svg = re.sub(r"<\?xml[^>]*\?>", "", svg)
    return f"<div style='width:100%;height:100%'>{svg}</div>"


def _image_html(block: Block, images: dict) -> str:
    path = images.get(block.block_id) or images.get(block.figure_id or "")
    if not path or not Path(path).exists():
        return ""
    return f"<img class='pic' src='{Path(path).resolve().as_uri()}'>"


def block_html(block: Block, style: StyleProfile, texts: dict, tables: dict, charts: dict,
               images: dict, number: int, looks: dict | None = None) -> str:
    if block.kind == "table":
        size = text_style(style, block).size_pt if block.style_id else 8.0
        return _table_html(block, tables, style, size, (looks or {}).get(block.block_id))
    if block.kind == "chart":
        return _chart_html(block, charts)
    if block.kind in {"photo", "logo"}:
        return _image_html(block, images)
    if block.kind == "page_number":
        return f"<span class='pageno'>{number}</span>"
    if block.kind == "decoration":
        return ""
    if block.kind in {"header", "footer"}:
        text = texts.get(block.block_id, block.text or "")    # running page furniture (publisher line)
    else:
        # never the SOURCE wording (task 6, E-36): a box without new text stays empty. Live run
        # 2026-10-26: unwritten boxes printed the source paragraph and headings word for word
        text = texts.get(block.block_id) or ""
    return f"<div class='txt text-{block.style_id or 'fallback'}'>{_paragraphs(text)}</div>"


def _block_css(block: Block, style: StyleProfile) -> str:
    x0, y0, x1, y1 = block.bbox
    return (f"left:{x0:.2f}pt;top:{y0:.2f}pt;width:{max(1.0, x1 - x0):.2f}pt;"
            f"height:{max(1.0, y1 - y0):.2f}pt")


FURNITURE_KINDS = {"header", "footer", "logo", "page_number"}
FLOW_SLACK = 0.95              # use this share of the body height: the height estimate is approximate


def _estimate_height(block: Block, html: str, style: StyleProfile, width: float, tables: dict) -> float:
    """Rough rendered height (pt) of a flow block, to decide when a flow page continues on a new one."""
    size = text_style(style, block).size_pt if block.style_id else 9.0
    if block.kind == "table":
        frame = tables.get(block.table_id or "")
        rows = (len(frame) + 1) if frame is not None else 1
        return rows * size * 1.6 + 6
    if block.kind in {"chart", "photo"}:
        w, h = max(block.bbox[2] - block.bbox[0], 1.0), max(block.bbox[3] - block.bbox[1], 1.0)
        return min(h * min(width / w, 1.5), 0.5 * 842) + 6
    text = re.sub(r"<[^>]+>", " ", html)
    per_line = max(width / (0.5 * size), 10)
    paragraphs = [p for p in re.split(r"</p>", html) if re.sub(r"<[^>]+>", "", p).strip()] or [text]
    lines = sum(max(1, -(-len(re.sub(r"<[^>]+>", "", p)) // int(per_line))) for p in paragraphs)
    return lines * size * text_style(style, block).line_height + 8 * len(paragraphs) + 6


def page_models(templates: list[PageTemplate], texts: dict, style: StyleProfile, tables: dict,
                charts: dict, images: dict, decorations: dict[int, str] | None = None,
                bands: dict[int, tuple[float, float]] | None = None,
                fills: dict[int, list[tuple[tuple, str]]] | None = None,
                looks: dict[str, dict] | None = None) -> list[dict]:
    pages = []
    for page in templates:
        blocks = []
        ordered = list(page.blocks)
        furniture: list[dict] = []
        if page.mode == "flow":                 # reading order; a table is followed by its chart
            # the masthead and footer bands keep their exact source positions (logo, band, company
            # name), so a flow page still looks like its source; only the body between them flows.
            # Live run 2026-10-26: flow pages dropped all of it and lost the branded look.
            top, bottom = (bands or {}).get(page.page, (0.0, style.page_height_pt))
            fixed = [b for b in page.blocks if b.kind in FURNITURE_KINDS
                     or (b.kind != "decoration" and (b.bbox[3] <= top + 2 or b.bbox[1] >= bottom - 2))]
            fixed_ids = {b.block_id for b in fixed}
            for block in fixed:
                furniture.append({"id": block.block_id, "kind": block.kind, "css": _block_css(block, style),
                                  "ink": _ink(block, style, (fills or {}).get(page.page, [])),
                                  "html": block_html(block, style, texts, tables, charts, images, page.page, looks)})
            ordered = [block for block in ordered
                       if block.kind != "decoration" and block.block_id not in fixed_ids]
            ordered = sorted(ordered, key=lambda block: (round(block.bbox[1], 1), block.bbox[0]))
            tables_on_page = {block.table_id for block in ordered
                              if block.kind == "table" and block.table_id}
            chart_for = {block.table_id: block for block in ordered
                         if block.kind == "chart" and block.table_id in tables_on_page}
            moved: set[str] = set()
            result = []
            for block in ordered:
                if block.kind == "chart" and block.table_id in chart_for:
                    continue
                result.append(block)
                if block.kind == "table" and block.table_id in chart_for:
                    result.append(chart_for[block.table_id])
                    moved.add(block.table_id)
            ordered = result + [block for block in ordered
                                if block.kind == "chart" and block.table_id not in moved]
        for block in ordered:
            blocks.append({"id": block.block_id, "kind": block.kind,
                           "css": _block_css(block, style),
                           "ink": _ink(block, style, (fills or {}).get(page.page, [])),
                           "html": block_html(block, style, texts, tables, charts, images,
                                              page.page, looks)})
        if page.mode != "flow":
            pages.append({"number": page.page, "mode": page.mode,
                          "w": style.page_width_pt, "h": style.page_height_pt,
                          "inner_css": _inner_css(style),
                          "decoration": (decorations or {}).get(page.page, ""), "blocks": blocks,
                          "furniture": []})
            continue
        # flow: fill the body between the bands. A page whose source was laid out in columns keeps
        # columns (M8.2.2 reason "columns"); one column would stack the same content two pages tall and
        # break the one-template-one-page rule (M8.11.2, QA `pages`).
        top, bottom = (bands or {}).get(page.page, (0.0, style.page_height_pt))
        m_top, m_right, m_bottom, m_left = style.margins_pt
        width = style.page_width_pt - m_left - m_right
        columns = 2 if page.reason == "columns" else 1
        width = (width - 18.0 * (columns - 1)) / columns     # blocks are measured at the COLUMN width
        first_top = max(m_top, top + 6)
        last_bottom = min(style.page_height_pt - m_bottom, bottom - 6)
        source = {b.block_id: b for b in page.blocks}
        # ONE sheet per template (M8.11.2, QA `pages`). WeasyPrint ignores column-count on a positioned
        # box, so the columns are explicit: blocks fill column 1 in reading order, then column 2. A block
        # that fits nowhere stays at the end of the last column (reading order kept; the box clips what
        # really overflows) and is listed in `overflow`, which QA reports. A continuation sheet broke
        # the page count (live run 2026-10-26).
        room = (last_bottom - first_top) * FLOW_SLACK
        cols: list[list[dict]] = [[] for _ in range(columns)]
        used = [0.0] * columns
        overflow: list[str] = []
        column = 0
        for item in blocks:
            block = source.get(item["id"])
            if not item["html"] or block is None:
                continue
            need = _estimate_height(block, item["html"], style, width, tables)
            while column < columns - 1 and cols[column] and used[column] + need > room:
                column += 1
            target = column
            if used[target] + need > room and cols[target]:
                overflow.append(item["id"])
            cols[target].append(item)
            used[target] += need
        gap = 18.0
        boxes = [{"css": _column_css(m_left + c * (width + gap), first_top, width, last_bottom),
                  "blocks": col} for c, col in enumerate(cols)]
        pages.append({"number": page.page, "mode": "flow",
                      "w": style.page_width_pt, "h": style.page_height_pt,
                      "inner_css": _inner_css(style, first_top, last_bottom),
                      "columns": boxes, "overflow": overflow,
                      "decoration": (decorations or {}).get(page.page, ""),
                      "blocks": [item for col in cols for item in col],
                      "furniture": furniture})
    return pages


def _column_css(left: float, top: float, width: float, bottom: float) -> str:
    return (f"position:absolute;left:{left:.2f}pt;top:{top:.2f}pt;width:{width:.2f}pt;"
            f"height:{max(bottom - top, 10.0):.2f}pt;overflow:hidden")


def _inner_css(style: StyleProfile, top: float | None = None, bottom: float | None = None,
               columns: int = 1) -> str:
    m_top, right, m_bottom, left = style.margins_pt
    top = m_top if top is None else top
    bottom = style.page_height_pt - m_bottom if bottom is None else bottom
    column_css = (f"column-count:{columns};column-gap:18pt;column-fill:auto;"
                  if columns > 1 else "")
    return (f"position:absolute;left:{left}pt;top:{top}pt;"
            f"width:{style.page_width_pt - left - right}pt;"
            f"height:{max(bottom - top, 10.0)}pt;overflow:hidden;{column_css}")


def _luminance(hex_colour: str) -> float:
    value = (hex_colour or "#000000").lstrip("#")[:6].ljust(6, "0")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _ink(block: Block, style: StyleProfile, fills: list[tuple[tuple, str]]) -> str:
    """'ink-light'/'ink-dark' when a fixed text box sits on a band its style colour would vanish on
    (live run 2026-10-26: the teal title on the teal masthead); '' otherwise."""
    if block.kind in {"logo", "photo", "chart", "table"} or not fills:
        return ""
    cx, cy = (block.bbox[0] + block.bbox[2]) / 2, (block.bbox[1] + block.bbox[3]) / 2
    for (x0, y0, x1, y1), fill in reversed(fills):        # topmost drawing wins
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            text, back = _luminance(text_style(style, block).color), _luminance(fill)
            ratio = (max(text, back) + 0.05) / (min(text, back) + 0.05)
            if ratio >= 3.0:
                return ""
            return "ink-light" if back < 0.4 else "ink-dark"
    return ""


def band_fills(pdf_path, page_number: int, width: float, height: float) -> list[tuple[tuple, str]]:
    """Filled rectangles at least 30% of the page wide, in drawing order, as ((x0, y0, x1, y1), '#RRGGBB')."""
    out = []
    try:
        import fitz
        with fitz.open(pdf_path) as document:
            for drawing in document[page_number - 1].get_drawings():
                r, fill = drawing["rect"], drawing.get("fill")
                if fill and r.width >= 0.3 * width:
                    out.append(((r.x0, r.y0, r.x1, r.y1), _colour(fill)))
    except Exception:
        return []
    return out


def page_bands(pdf_path, page_number: int, width: float, height: float) -> tuple[float, float]:
    """(masthead bottom, footer top) in pt from the source page's wide band drawings: a band at least
    60% of the page wide that lies in the top 45% is masthead; one starting in the bottom 15% is footer."""
    top, bottom = 0.0, height
    try:
        import fitz
        with fitz.open(pdf_path) as document:
            for drawing in document[page_number - 1].get_drawings():
                r = drawing["rect"]
                if r.width < 0.6 * width:
                    continue
                if r.y1 <= 0.45 * height:
                    top = max(top, float(r.y1))
                elif r.y0 >= 0.85 * height:
                    bottom = min(bottom, float(r.y0))
    except Exception:                          # no source drawings: whole page is body
        return 0.0, height
    return top, bottom


def render_document(pages: list[dict], style: StyleProfile, artifacts_root: Path | None,
                    title: str = "", adjustments: dict | None = None, lang: str = "en") -> str:
    env = _env()
    mirrored = env.get_template("page_mirror.html.j2")
    flowed = env.get_template("page_flow.html.j2")
    css = css_for(style, artifacts_root, adjustments)
    body = []
    for page in pages:
        template = flowed if page["mode"] == "flow" else mirrored
        body.append(template.render(page=page))
    # v3.3 F-10: <html lang> lets WeasyPrint hyphenate justified text in the source's language
    return (f"<!doctype html><html lang='{html_lib.escape(lang or 'en')}'><head><meta charset='utf-8'>"
            f"<title>{html_lib.escape(title)}</title><style>{css}</style></head><body>"
            + "".join(body) + "</body></html>")


def decorations(pdf_path, page_number: int, width: float, height: float,
                avoid: list[Block], body: tuple[float, float] | None = None) -> str:
    """Page furniture from the source drawings, as one SVG layer behind the blocks: header/footer bands,
    rules at least half the page wide and large plain panels. Never a chart's marks: a source chart drawn
    as vectors (lines, bars) would carry the REAL data into the output (task 6), so complex paths, small
    rectangles and anything touching a table, chart, photo, logo or axis-label box are left out."""
    try:
        import fitz
    except Exception:
        return ""
    try:
        document = fitz.open(pdf_path)
        page = document[page_number - 1]
        page_area = max(width * height, 1.0)
        # a box covering half the page or more is the page background, not a chart: keeping it
        # would hide every band inside it (live run 2026-10-26: page 1 lost its teal masthead)
        boxes = [fitz.Rect(block.bbox) for block in avoid
                 if block.kind in {"table", "chart", "photo", "logo", "decoration"}
                 and fitz.Rect(block.bbox).get_area() < 0.5 * page_area]
        parts: list[str] = []
        for drawing in page.get_drawings():
            rect = fitz.Rect(drawing["rect"])
            area = max(rect.width * rect.height, 1.0)
            # chart marks lie INSIDE a chart/table box; a masthead band merely CONTAINS the logo box
            if any((rect & box).get_area() >= 0.6 * area for box in boxes if rect.intersects(box)):
                continue
            if not _furniture(rect, drawing.get("items", []), width, height, page_area):
                continue
            if body and rect.y0 >= body[0] - 1 and rect.y1 <= body[1] + 1:
                continue                        # flow body reflows: a panel there would sit behind the wrong content
            fill = _colour(drawing.get("fill"))
            stroke = _colour(drawing.get("color"))
            width_pt = float(drawing.get("width") or 0)
            attrs = (f"fill='{fill}' stroke='{stroke}' stroke-width='{width_pt:.2f}'"
                     if fill or stroke else "")
            segments = [item for item in drawing["items"] if item[0] == "l"]
            if fill != "none" and segments:
                # a filled outline made of line segments (the masthead's slanted band) is ONE closed
                # shape; separate <line>s cannot be filled (live run 2026-10-26: it vanished)
                d, last = [], None
                for _, p1, p2 in segments:
                    if last is None or abs(p1.x - last.x) > 0.01 or abs(p1.y - last.y) > 0.01:
                        d.append(f"M{p1.x:.2f},{p1.y:.2f}")
                    d.append(f"L{p2.x:.2f},{p2.y:.2f}")
                    last = p2
                parts.append(f"<path d='{' '.join(d)} Z' {attrs}/>")
            for item in drawing["items"]:
                kind = item[0]
                if kind == "l" and fill != "none":
                    continue
                if kind == "re":
                    x0, y0, x1, y1 = item[1]
                    parts.append(f"<rect x='{x0:.2f}' y='{y0:.2f}' width='{x1 - x0:.2f}' "
                                 f"height='{y1 - y0:.2f}' {attrs}/>")
                elif kind == "l":
                    p1, p2 = item[1], item[2]
                    parts.append(f"<line x1='{p1.x:.2f}' y1='{p1.y:.2f}' x2='{p2.x:.2f}' "
                                 f"y2='{p2.y:.2f}' {attrs}/>")
                elif kind == "qu":
                    quad = item[1]
                    points = " ".join(f"{point.x:.2f},{point.y:.2f}" for point in quad)
                    parts.append(f"<polygon points='{points}' {attrs}/>")
        return (f"<svg class='deco' viewBox='0 0 {width} {height}' "
                f"xmlns='http://www.w3.org/2000/svg'>{''.join(parts)}</svg>")
    except Exception:
        return ""


FURNITURE_BAND = 0.12          # top/bottom share of the page that holds header/footer bands
MAX_FURNITURE_ITEMS = 6        # more path pieces than this = a chart line or icon, not furniture


def _furniture(rect, items: list, width: float, height: float, page_area: float) -> bool:
    """True for a header/footer band, a wide rule or a large panel; False for chart marks."""
    if len(items) > MAX_FURNITURE_ITEMS or any(item[0] not in {"re", "l", "qu"} for item in items):
        return False                            # curves and long polylines: chart lines, icons, logos
    in_band = rect.y1 <= FURNITURE_BAND * height or rect.y0 >= (1 - FURNITURE_BAND) * height
    wide_rule = rect.width >= 0.5 * width and rect.height <= 3.0
    panel = rect.width * rect.height >= 0.04 * page_area and rect.width >= 0.3 * width
    # E-48: the source layout is kept, so small plain shapes (a rating box, a label strip, a rule under
    # a heading) are drawn too. Chart marks never reach here: every detected chart is an avoid box.
    # Only WIDE shapes and plain lines: a tall narrow filled rectangle may be a chart bar the detector
    # missed (a real data shape), so it is never copied.
    small_shape = len(items) <= 2 and all(item[0] in {"re", "l"} for item in items) and (
        all(item[0] == "l" for item in items) or rect.width >= 1.5 * max(rect.height, 0.1))
    return in_band or wide_rule or panel or small_shape


def _colour(value) -> str:
    if not value:
        return "none"
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return "#{:02X}{:02X}{:02X}".format(*(int(round(min(1.0, max(0.0, x)) * 255))
                                             for x in value[:3]))
    return str(value)


def write_metadata_v32(pdf_path: Path, title: str, author: str = "", subject: str = "",
                       keywords: str = "", run_id: str = "", source_pdf=None) -> None:
    """Document Info and XMP like an ordinary published document (E-32, overriding D-07/M8.11.3): the
    title, the organisation as author and the SOURCE PDF's own creator/producer. No synthetic marker,
    no run id, no tool name, no subject/keywords: synthetic status lives only in the Run Manifest.
    `subject`, `keywords` and `run_id` are accepted for compatibility and deliberately not written."""
    import pymupdf

    creator = producer = ""
    if source_pdf and Path(source_pdf).exists():
        try:
            with pymupdf.open(str(source_pdf)) as source:
                creator = (source.metadata or {}).get("creator", "") or ""
                producer = (source.metadata or {}).get("producer", "") or ""
        except Exception:                      # unreadable source metadata: leave them empty
            pass
    xmp = (f"<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d'?>"
           "<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF "
           "xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
           "<rdf:Description rdf:about='' xmlns:dc='http://purl.org/dc/elements/1.1/'>"
           f"<dc:title><rdf:Alt><rdf:li xml:lang='x-default'>{html_lib.escape(title)}</rdf:li></rdf:Alt></dc:title>"
           f"<dc:creator><rdf:Seq><rdf:li>{html_lib.escape(author)}</rdf:li></rdf:Seq></dc:creator>"
           "</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end='w'?>")
    with pymupdf.open(str(pdf_path)) as document:
        document.set_metadata({"title": title, "author": author, "subject": "", "keywords": "",
                               "creator": creator, "producer": producer})
        document.set_xml_metadata(xmp)
        temp = Path(pdf_path).with_suffix(".meta.pdf")
        document.save(str(temp))
    temp.replace(pdf_path)


def render_pages(templates: list[PageTemplate], texts: dict, style: StyleProfile, tables: dict,
                 charts: dict, images: dict, out_dir: Path, artifacts_root: Path | None,
                 pdf_path, meta: dict | None = None, decorate: bool = True,
                 adjustments: dict | None = None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    decoration_layers, bands, fills = {}, {}, {}
    if decorate and pdf_path:
        for page in templates:              # flow pages keep their bands too (masthead, footer)
            body = None
            if page.mode == "flow":
                bands[page.page] = body = page_bands(pdf_path, page.page, style.page_width_pt,
                                                     style.page_height_pt)
            # every page: text on a coloured band gets a readable ink (mirror pages too, E-48)
            fills[page.page] = band_fills(pdf_path, page.page, style.page_width_pt,
                                          style.page_height_pt)
            decoration_layers[page.page] = decorations(pdf_path, page.page, style.page_width_pt,
                                                       style.page_height_pt, page.blocks, body)
    looks = table_looks(pdf_path, templates) if pdf_path else {}
    pages = page_models(templates, texts, style, tables, charts, images, decoration_layers, bands,
                        fills, looks)
    document = render_document(pages, style, artifacts_root,
                               (meta or {}).get("title", ""), adjustments, (meta or {}).get("lang", "en"))
    html_path = out_dir / "index.html"
    html_path.write_text(document, encoding="utf-8")
    pdf_out = out_dir / "output.pdf"
    from src.rendering import _prepare_weasyprint

    _prepare_weasyprint()
    from weasyprint import HTML

    HTML(string=document, base_url=str(out_dir)).write_pdf(str(pdf_out))
    meta = meta or {}
    write_metadata_v32(pdf_out, meta.get("title", ""), meta.get("author", ""), source_pdf=pdf_path)
    pngs = _page_images(pdf_out, out_dir / "pages", pdf_path)
    return {"pdf": str(pdf_out), "html": str(html_path), "pages": len(pages),
            "pngs": pngs["output"], "side_by_side": pngs["side_by_side"],
            "decorated_pages": sum(1 for layer in decoration_layers.values() if layer),
            "flow_overflow": [block_id for page in pages for block_id in page.get("overflow", [])]}


def _page_images(pdf_out: Path, png_dir: Path, source_pdf) -> dict:
    import pymupdf
    from PIL import Image

    png_dir.mkdir(parents=True, exist_ok=True)
    output, side = [], []
    with pymupdf.open(str(pdf_out)) as document:
        for index, page in enumerate(document):
            path = png_dir / f"p{index + 1}.png"
            page.get_pixmap(dpi=110).save(str(path))
            output.append(str(path))
            if source_pdf and Path(source_pdf).exists():
                with pymupdf.open(str(source_pdf)) as source:
                    if index < len(source):
                        left = png_dir / f"_source_p{index + 1}.png"
                        source[index].get_pixmap(dpi=110).save(str(left))
                        with Image.open(left) as a, Image.open(path) as b:
                            canvas = Image.new("RGB", (a.width + b.width + 10,
                                                       max(a.height, b.height)), "white")
                            canvas.paste(a.convert("RGB"), (0, 0))
                            canvas.paste(b.convert("RGB"), (a.width + 10, 0))
                            pair = png_dir / f"side_by_side_p{index + 1}.png"
                            canvas.save(pair)
                            side.append(str(pair))
    return {"output": output, "side_by_side": side}
