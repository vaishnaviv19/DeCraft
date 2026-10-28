# tests/rendering/test_qa.py

import pymupdf

from src.contracts.report import Block, FontSpec, PageTemplate, StyleProfile, TextStyle
from src.rendering._measure import _bundled_font
from src.rendering._pages import render_pages
from src.rendering._qa_pages import check_pages


def style() -> StyleProfile:
    return StyleProfile(page_width_pt=200, page_height_pt=400, margins_pt=(20, 20, 20, 20),
                        fonts=[FontSpec(font_id="F1", source_name="x", family="DejaVu Sans",
                                        file=_bundled_font(False), fallback="DejaVu Sans")],
                        text_styles=[TextStyle(style_id="T1", font_id="F1", size_pt=10,
                                               color="#112233", line_height=1.2, align="left")],
                        palette=["#112233"])


def template(block: Block) -> PageTemplate:
    return PageTemplate(page=1, mode="mirror", reason="", blocks=[block])


def run(pdf_path, block: Block):
    return check_pages(pdf_path, [template(block)], style(), {}, {}, set(), None,
                       min_words=1)


def test_a_clean_render_passes_every_gate(tmp_path):
    block = Block(block_id="p1b01", kind="text", bbox=(10, 20, 190, 120), style_id="T1")
    result = render_pages([template(block)], {"p1b01": "Hello synthetic report."}, style(),
                          {}, {}, {}, tmp_path, None, None, {"title": "Clean"})
    report = run(result["pdf"], block)
    assert all(report["gates"].values()), report["failures"]
    assert report["failures"] == []


def test_a_leftover_placeholder_fails_the_placeholders_gate(tmp_path):
    path = tmp_path / "placeholder.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=400)
    page.insert_text((10, 40), "Hello {{x}}", fontsize=10)
    document.save(str(path))
    document.close()
    block = Block(block_id="p1b01", kind="text", bbox=(0, 10, 200, 120), style_id="T1")
    report = run(path, block)
    assert report["gates"]["placeholders"] is False
    assert any("placeholder" in failure for failure in report["failures"])


def test_a_text_block_outside_its_box_fails(tmp_path):
    path = tmp_path / "outside.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=400)
    page.insert_text((10, 300), "This text sits far below its block.", fontsize=10)
    document.save(str(path))
    document.close()
    block = Block(block_id="p1b01", kind="text", bbox=(0, 10, 200, 120), style_id="T1")
    report = run(path, block)
    assert report["gates"]["text_in_boxes"] is False
    assert any("text_in_boxes" in failure for failure in report["failures"])


def _pdf(path, lines, width=200, height=400):
    document = pymupdf.open()
    page = document.new_page(width=width, height=height)
    y = 40
    for line in lines:
        page.insert_text((10, y), line, fontsize=10)
        y += 14
    document.save(str(path))
    document.close()


def test_a_common_word_capitalised_at_a_sentence_start_is_not_a_source_name(tmp_path):
    source = tmp_path / "source.pdf"
    _pdf(source, ["Capacity expansion continued. The capacity grew again in the region this year."])
    path = tmp_path / "output.pdf"
    _pdf(path, ["Capacity improved."])
    block = Block(block_id="p1b01", kind="text", bbox=(8, 20, 190, 120), style_id="T1")
    report = check_pages(path, [template(block)], style(), {}, {}, {"Capacity"}, source, min_words=1)
    assert report["gates"]["no_source_names"] is True


def test_a_single_word_name_used_as_a_name_in_the_source_is_caught(tmp_path):
    source = tmp_path / "source.pdf"
    _pdf(source, ["The company opened a new office in Pune last year."])
    path = tmp_path / "output.pdf"
    _pdf(path, ["Pune is a growing market."])
    block = Block(block_id="p1b01", kind="text", bbox=(8, 20, 190, 120), style_id="T1")
    report = check_pages(path, [template(block)], style(), {}, {}, {"Pune"}, source, min_words=1)
    assert report["gates"]["no_source_names"] is False
    assert any("no_source_names" in failure for failure in report["failures"])


def test_a_label_kept_in_a_table_is_not_a_reused_source_name(tmp_path):
    import pandas as pd

    path = tmp_path / "output.pdf"
    _pdf(path, ["Accounts Receivable fell."])
    block = Block(block_id="p1b01", kind="text", bbox=(8, 20, 190, 120), style_id="T1")
    display = {"t1": pd.DataFrame({"Line": ["Accounts Receivable"], "FY24": [42.0]})}
    report = check_pages(path, [template(block)], style(), display, {},
                         {"Accounts Receivable"}, None, min_words=1)
    assert report["gates"]["no_source_names"] is True


def test_page_furniture_may_reach_the_trim_edge(tmp_path):
    path = tmp_path / "furniture.pdf"
    _pdf(path, ["Body text."])
    text = Block(block_id="p1b01", kind="text", bbox=(8, 20, 190, 120), style_id="T1")
    edge = Block(block_id="p1b02", kind="decoration", bbox=(-15, 0, 598, 840))
    report = check_pages(path, [template(text).model_copy(update={"blocks": [edge, text]})],
                         style(), {}, {}, set(), None, min_words=1)
    assert report["gates"]["layout_fits"] is True
