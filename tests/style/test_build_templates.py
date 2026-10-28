# tests/style/test_build_templates.py
from pathlib import Path

import pymupdf

from src.config import load_config
from src.core.rows import statement_label_column  # noqa: F401  (kept for symmetry with the style module)
from src.style import build_templates, extract_style
from src.style._templates import _overlaps

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "input" / "sample_input_1.pdf"


def classified_page(path: Path) -> Path:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 100), "Annual Performance Review", fontsize=18, fontname="hebo")
    page.insert_textbox(pymupdf.Rect(72, 180, 520, 240),
                        "First paragraph of the body text that explains the year in a few words.",
                        fontsize=10, fontname="helv")
    page.insert_textbox(pymupdf.Rect(72, 250, 520, 310),
                        "Second paragraph continues the discussion with more detail.",
                        fontsize=10, fontname="helv")
    for y in (400, 420, 440):
        page.draw_line((72, y), (300, y))
    for x in (72, 300):
        page.draw_line((x, 400), (x, 440))
    page.insert_text((72, 455), "Table 1: numbers", fontsize=8, fontname="helv")
    page.insert_text((300, 820), "1", fontsize=8, fontname="helv")
    document.save(path)
    document.close()
    return path


def template_for(path: Path):
    cfg = load_config()
    style = extract_style(path, cfg)
    return build_templates(path, None, style, cfg)


def test_blocks_are_classified(tmp_path):
    pages = template_for(classified_page(tmp_path / "kinds.pdf"))
    assert len(pages) == 1
    kinds = [block.kind for block in pages[0].blocks]
    assert kinds == ["heading", "text", "text", "table", "caption", "page_number"], kinds
    assert all(block.source_words > 0 for block in pages[0].blocks if block.kind != "table")
    assert all(block.block_id.startswith("p1b") for block in pages[0].blocks)


def test_a_rotated_page_flows(tmp_path):
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 100), "Rotated body text", fontsize=10, fontname="helv")
    page.set_rotation(90)
    path = tmp_path / "rotated.pdf"
    document.save(path)
    document.close()
    page_template = template_for(path)[0]
    assert page_template.mode == "flow" and page_template.reason == "rotated"


def test_many_columns_stay_mirrored(tmp_path):
    """E-48 (owner decision): the source layout is kept; a many-column page is mirrored, reason recorded."""
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    for n, (x0, x1) in enumerate(((36, 160), (170, 290), (300, 420), (430, 560))):
        page.insert_textbox(pymupdf.Rect(x0, 100, x1, 400),
                            f"Column {n} text line one.\nColumn {n} text line two.",
                            fontsize=8, fontname="helv")
    path = tmp_path / "columns.pdf"
    document.save(path)
    document.close()
    page_template = template_for(path)[0]
    assert page_template.mode == "mirror" and page_template.reason.startswith("columns")


def test_overlap_helper():
    assert _overlaps((0, 0, 10, 10), (5, 5, 15, 15))
    assert not _overlaps((0, 0, 10, 10), (50, 50, 60, 60))


def test_sections_start_at_headings_and_front_matter_is_s0(tmp_path):
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(72, 80, 520, 130), "Introductory front matter text.",
                        fontsize=10, fontname="helv")
    page.insert_text((72, 200), "First Heading", fontsize=16, fontname="hebo")
    page.insert_textbox(pymupdf.Rect(72, 220, 520, 270), "Body of the first section.",
                        fontsize=10, fontname="helv")
    page.insert_text((72, 320), "Second Heading", fontsize=16, fontname="hebo")
    page.insert_textbox(pymupdf.Rect(72, 340, 520, 390), "Body of the second section.",
                        fontsize=10, fontname="helv")
    path = tmp_path / "sections.pdf"
    document.save(path)
    document.close()
    blocks = template_for(path)[0].blocks
    assert [(b.kind, b.section_id) for b in blocks] == [
        ("text", "S0"), ("heading", "S1"), ("text", "S1"), ("heading", "S2"), ("text", "S2")]


def test_the_real_sample_has_a_template_per_page_and_every_block_owned():
    cfg = load_config()
    style = extract_style(SAMPLE, cfg)
    pages = build_templates(SAMPLE, None, style, cfg)
    assert len(pages) == 5
    ids = set()
    for page in pages:
        assert page.mode in {"mirror", "flow"}
        assert page.mode == "mirror" or page.reason
        for block in page.blocks:
            assert block.block_id not in ids
            ids.add(block.block_id)
            assert block.section_id is not None
    kinds = {block.kind for page in pages for block in page.blocks}
    assert {"text", "heading"} <= kinds


def test_borderless_table_rows_become_one_table_block_not_writer_text(tmp_path):
    """Live run 2026-10-26: 105 of 132 'text' boxes were table rows or axis labels."""
    import pymupdf
    from src.config import load_config
    from src.style import build_templates, extract_style
    path = tmp_path / "rows.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(72, 80, 520, 160), "Revenue rose this year as demand improved across "
                        "every region, and the margin held steady despite higher input costs.", fontsize=10)
    for i, (label, a, b, c) in enumerate([("Revenue", "32,703", "42,816", "55,109"), ("EBITDA", "7,442", "8,431", "9,144"),
                                          ("Tax", "1,051", "1,203", "1,388")]):
        y = 200 + i * 14
        for x, cell in ((72, label), (220, a), (320, b), (420, c)):
            page.insert_text((x, y), cell, fontsize=9)
    page.insert_text((72, 320), "280", fontsize=8)                       # a chart axis label
    doc.save(path)
    doc.close()
    cfg = load_config(llm_enabled=False)
    pages = build_templates(path, None, extract_style(path, cfg), cfg)
    kinds = [b.kind for b in pages[0].blocks]
    assert kinds.count("text") == 1 and kinds.count("table") == 1        # three rows -> one table region
    assert "decoration" in kinds
