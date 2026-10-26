# src/rendering/_qa_pages.py
"""Deterministic PDF gates (M8.12.1) for the v3.2 page templates.

Besides the v3 checks (pages, placeholders, words) it verifies that font sizes equal the Text Styles
(E-29), no source name reappears (E-28), the page count equals the template count, per-page words are
within +/-15% of the source page (E-13), every chartable displayed table has a chart whose sidecar
equals the table, fonts are embedded, and every text block sits inside a template block box (2 pt)."""
from __future__ import annotations

import json
from pathlib import Path

from src.contracts.report import PageTemplate, StyleProfile
from src.core.originality import drop_shown, name_words, reused_names
from src.rendering._pages import text_style


def _sizes(templates: list[PageTemplate], style: StyleProfile) -> set[float]:
    sizes = set()
    for page in templates:
        for block in page.blocks:
            if block.kind in {"decoration", "photo", "logo", "chart"}:
                continue
            sizes.add(round(8.0 if block.kind == "table" and not block.style_id
                            else text_style(style, block).size_pt, 1))
    return sizes


def _span_sizes(page) -> list[float]:
    values = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span.get("text", "").strip():
                    values.append(round(float(span.get("size", 0)), 1))
    return values


def _inside(inner, outer, tolerance: float = 2.0) -> bool:
    return (inner[0] >= outer[0] - tolerance and inner[1] >= outer[1] - tolerance
            and inner[2] <= outer[2] + tolerance and inner[3] <= outer[3] + tolerance)


def _name_like(avoid: set[str], source_pdf) -> set[str]:
    """E-28: only names. A single ordinary word capitalised at a sentence start or sitting in a table
    (e.g. 'Capacity', 'Each') is not a proper noun; the source's prose must use it as a name. Multi-word
    candidates stay candidates. Never reads the source when it is not given."""
    if not source_pdf or not Path(source_pdf).exists():
        return set(avoid)
    import pymupdf

    with pymupdf.open(str(source_pdf)) as source:
        prose = " ".join(page.get_text() for page in source)
    names = name_words(prose)
    return {name for name in avoid if len(name.split()) > 1 or name.lower() in names}


def check_pages(pdf_path: Path, templates: list[PageTemplate], style: StyleProfile,
                tables: dict | None = None, sidecars: dict[str, Path] | None = None,
                avoid: set[str] | None = None, source_pdf=None, min_words: int = 1000,
                word_tolerance: float = 0.15, unchartable: set[str] | None = None) -> dict:
    import pymupdf

    tables = tables or {}
    sidecars = sidecars or {}
    unchartable = unchartable or set()
    gates: dict[str, bool] = {}
    failures: list[str] = []
    document = pymupdf.open(str(pdf_path))
    pages = document.page_count

    gates["pages"] = pages == len(templates)
    if not gates["pages"]:
        failures.append(f"pages: {pages} != {len(templates)} templates")

    text = "\n".join(page.get_text() for page in document)
    gates["placeholders"] = "{{" not in text and "}}" not in text
    if not gates["placeholders"]:
        failures.append("placeholders: a '{{...}}' template marker survived into the PDF")

    total_words = len(text.split())
    gates["words"] = total_words >= min_words
    if not gates["words"]:
        failures.append(f"words: {total_words} < {min_words}")

    if source_pdf and Path(source_pdf).exists():
        source = pymupdf.open(str(source_pdf))
        per_page = True
        for index, page in enumerate(document):
            if index >= len(source):
                break
            expected = len(source[index].get_text().split())
            actual = len(page.get_text().split())
            if expected >= 40 and abs(actual - expected) > expected * word_tolerance:
                per_page = False
                failures.append(f"words_per_page: page {index + 1} has {actual} words, source {expected}")
        gates["words_per_page"] = per_page

    allowed = _sizes(templates, style)
    locked = True
    for index, page in enumerate(document):
        for size in _span_sizes(page):
            if not any(abs(size - value) <= 0.25 for value in allowed):
                locked = False
                failures.append(f"font_sizes_locked: page {index + 1} has a {size}pt span "
                                f"(allowed {sorted(allowed)})")
                break
        if not locked:
            break
    gates["font_sizes_locked"] = locked

    # E-28: a source name may not reappear, except vocabulary the output's own tables show (the
    # privacy plan's `avoid` keeps those names, e.g. kept standard labels; see `drop_shown`).
    shown = [str(value) for frame in tables.values() for value in frame.columns] + \
            [str(value) for frame in tables.values() for value in frame.to_numpy().ravel()
             if value is not None]
    source_names = reused_names(text, drop_shown(_name_like(set(avoid or ()), source_pdf), shown))
    gates["no_source_names"] = not source_names
    if source_names:
        failures.append(f"no_source_names: {sorted(source_names)[:5]}")

    fonts_ok = all(page.get_fonts() for page in document)
    gates["fonts_embedded"] = fonts_ok
    if not fonts_ok:
        failures.append("fonts_embedded: a page lists no embedded font")

    outside = True
    for index, page in enumerate(document):
        if index >= len(templates):
            break
        boxes = [block.bbox for block in templates[index].blocks]
        for block in page.get_text("blocks"):
            bbox = block[:4]
            if str(block[4]).strip() and not any(_inside(bbox, box) for box in boxes):
                outside = False
                failures.append(f"text_in_boxes: page {index + 1} text at "
                                f"{tuple(round(v, 1) for v in bbox)} is outside every block box")
                break
        if not outside:
            break
    gates["text_in_boxes"] = outside

    layout = True
    width, height = style.page_width_pt, style.page_height_pt
    for template in templates:
        for block in template.blocks:
            # page furniture and pictures may reach the trim edge (the source's own masthead does);
            # only boxes that carry text are required to sit on the page
            if block.kind in {"decoration", "photo", "logo", "chart"}:
                continue
            x0, y0, x1, y1 = block.bbox
            if x0 < -2 or y0 < -2 or x1 > width + 2 or y1 > height + 2:
                layout = False
                failures.append(f"layout_fits: {block.block_id} extends outside the page")
    gates["layout_fits"] = layout

    charted = True
    for page in templates:
        chart_tables = {block.table_id for block in page.blocks if block.kind == "chart"}
        for block in page.blocks:
            if block.kind != "table" or not block.table_id:
                continue
            if block.table_id in unchartable or block.table_id not in tables:
                continue
            # the chart must be ON the page (a chart block for this table) and drawn from its data
            # file; a data file alone passed this gate while no chart reached the PDF (2026-10-26)
            if block.table_id not in chart_tables or block.table_id not in sidecars:
                charted = False
                failures.append(f"tables_charted: {block.table_id} has no chart")
    gates["tables_charted"] = charted

    matching = True
    for table_id, path in sidecars.items():
        display = tables.get(table_id)
        if display is None or not Path(path).exists():
            continue
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        try:
            from src.rendering._charts import assert_sidecar_matches_display

            assert_sidecar_matches_display(payload, display)
        except AssertionError as error:
            matching = False
            failures.append(f"charts_match_tables: {table_id}: {error}")
    gates["charts_match_tables"] = matching
    return {"gates": gates, "failures": failures}
