# tests/rendering/test_measure.py
from src.contracts.report import Block, FontSpec, StyleProfile, TextStyle
from src.rendering._fit import fits
from src.rendering._measure import advance_width_fn, box_for, font_path, width_fn_for


def style() -> StyleProfile:
    return StyleProfile(page_width_pt=595, page_height_pt=842, margins_pt=(40, 40, 40, 40),
                        fonts=[FontSpec(font_id="F1", source_name="x", family="Liberation Sans",
                                        fallback="DejaVu Sans")],
                        text_styles=[TextStyle(style_id="T1", font_id="F1", size_pt=10,
                                               color="#000000", line_height=1.2)],
                        palette=["#000000"])


def test_proportional_widths_come_from_the_font_tables():
    path = font_path(style().fonts[0])
    assert path is not None
    width = advance_width_fn(path, 10)
    assert width("MMMM") > width("iiii")
    assert advance_width_fn(path, 20)("M") > advance_width_fn(path, 10)("M")


def test_a_block_gets_its_box_and_width_function_from_its_text_style():
    block = Block(block_id="p1b01", kind="text", bbox=(10, 20, 130, 50), style_id="T1",
                  text="source text here", source_words=3)
    box = box_for(block, style())
    assert (box.width_pt, box.height_pt, box.font_size, box.line_height) == (120, 30, 10, 1.2)
    ok, need, have = fits("A short line.", box, width_fn_for(block, style()))
    assert ok and have == 2 and need == 1
