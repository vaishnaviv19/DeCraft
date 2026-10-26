# tests/rendering/test_pages.py
from pathlib import Path

import pandas as pd
import pymupdf

from src.contracts.report import Block, FontSpec, PageTemplate, StyleProfile, TextStyle
from src.rendering._measure import _bundled_font
from src.rendering._pages import (page_models, render_document, render_pages, write_metadata_v32)


def style() -> StyleProfile:
    return StyleProfile(page_width_pt=200, page_height_pt=400, margins_pt=(20, 20, 20, 20),
                        fonts=[FontSpec(font_id="F1", source_name="x", family="DejaVu Sans",
                                        file=_bundled_font(False), fallback="DejaVu Sans")],
                        text_styles=[TextStyle(style_id="T1", font_id="F1", size_pt=10,
                                               color="#112233", line_height=1.2, align="left")],
                        palette=["#112233"])


def test_mirror_html_has_absolute_points_and_a_real_font():
    blocks = [Block(block_id="p1b01", kind="text", bbox=(10, 20, 110, 40), style_id="T1"),
              Block(block_id="p1b02", kind="heading", bbox=(20, 60, 160, 80), style_id="T1")]
    page = PageTemplate(page=1, mode="mirror", reason="", blocks=blocks)
    html = render_document(page_models([page], {"p1b01": "First.", "p1b02": "Second."},
                                       style(), {}, {}, {}), style(), Path("."))
    assert "left:10.00pt" in html and "top:20.00pt" in html and "width:100.00pt" in html
    assert "@font-face" in html and "position: absolute" in html
    font = style().fonts[0].file
    assert font and Path(font).exists() and Path(font).as_uri() in html


def test_flow_pages_put_a_chart_right_after_its_table(tmp_path):
    svg = tmp_path / "chart.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'><rect width='10' height='10'/></svg>")
    blocks = [Block(block_id="p1b01", kind="chart", bbox=(0, 10, 100, 60), table_id="t1"),
              Block(block_id="p1b02", kind="text", bbox=(0, 100, 100, 120), style_id="T1"),
              Block(block_id="p1b03", kind="table", bbox=(0, 200, 100, 260), table_id="t1")]
    page = PageTemplate(page=1, mode="flow", reason="", blocks=blocks)
    html = render_document(page_models([page], {"p1b02": "Some text."}, style(),
                                       {"t1": pd.DataFrame({"A": [1]})}, {"t1": str(svg)}, {}),
                           style(), None)
    order = [html.index(f'data-block="{block_id}"')
             for block_id in ("p1b02", "p1b03", "p1b01")]
    assert order == sorted(order)
    assert "break-inside: avoid" in html


def test_a_source_column_layout_flows_in_two_columns_on_one_sheet():
    blocks = [Block(block_id=f"p1b0{i}", kind="text", bbox=(0, i * 30, 180, i * 30 + 20),
                    style_id="T1") for i in range(1, 5)]
    page = PageTemplate(page=1, mode="flow", reason="columns", blocks=blocks)
    models = page_models([page], {block.block_id: "Some flow text." for block in blocks},
                         style(), {}, {}, {})
    assert len(models) == 1                                   # one sheet per template, always
    assert len(models[0]["columns"]) == 2                     # explicit columns (no CSS column-count)
    assert len(models[0]["blocks"]) == 4


def test_flow_content_taller_than_two_columns_stays_on_one_sheet_and_is_reported():
    blocks = [Block(block_id=f"p1b{i:02d}", kind="text", bbox=(0, 20 + i * 10, 180, 28 + i * 10),
                    style_id="T1") for i in range(1, 30)]
    page = PageTemplate(page=1, mode="flow", reason="columns", blocks=blocks)
    long_text = " ".join(["Words that need room."] * 30)
    models = page_models([page], {block.block_id: long_text for block in blocks}, style(), {}, {}, {})
    assert len(models) == 1 and models[0]["overflow"]
    assert sum(len(c["blocks"]) for c in models[0]["columns"]) == len(blocks)


MARKERS = ("synthetic", "decraft", "generated", "fake", "run-42", "run-7")


def test_pdf_metadata_looks_like_an_ordinary_document(tmp_path):
    """E-32 (owner decision, overrides M8.11.3): no synthetic marker, run id or tool name in the file."""
    source = tmp_path / "source.pdf"
    document = pymupdf.open()
    document.new_page()
    document.set_metadata({"creator": "Microsoft Word", "producer": "Acrobat Distiller"})
    document.save(str(source))
    document.close()
    path = tmp_path / "out.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(str(path))
    document.close()
    write_metadata_v32(path, "Acme Annual Update", "Acme Ltd", "Synthetic report", "synthetic", "run-42",
                       source_pdf=source)
    with pymupdf.open(str(path)) as opened:
        meta = opened.metadata
        xmp = opened.get_xml_metadata()
    assert meta["title"] == "Acme Annual Update" and meta["author"] == "Acme Ltd"
    assert meta["creator"] == "Microsoft Word" and meta["producer"] == "Acrobat Distiller"
    blob = (" ".join(str(v) for v in meta.values()) + xmp).lower()
    assert not any(marker in blob for marker in MARKERS)


def test_decorations_keep_page_furniture_but_never_chart_marks(tmp_path):
    from src.rendering._pages import decorations
    path = tmp_path / "deco.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(0, 0, 595, 40), color=(0, 0.2, 0.5), fill=(0, 0.2, 0.5))      # header band
    shape = page.new_shape()                                                                  # a price line
    points = [(100 + 20 * i, 400 - (i % 3) * 15) for i in range(12)]
    for p1, p2 in zip(points, points[1:]):
        shape.draw_line(p1, p2)
    shape.finish(color=(1, 0, 0))
    shape.commit()
    page.draw_rect(pymupdf.Rect(300, 500, 312, 560), color=(0, 0, 1), fill=(0, 0, 1))          # one chart bar
    document.save(str(path))
    document.close()
    svg = decorations(str(path), 1, 595, 842, [])
    assert svg.count("<rect") == 1 and "<line" not in svg                    # only the header band


def test_render_pages_writes_pdf_pngs_and_metadata(tmp_path):
    page = PageTemplate(page=1, mode="mirror", reason="", blocks=[
        Block(block_id="p1b01", kind="text", bbox=(10, 20, 190, 120), style_id="T1")])
    result = render_pages([page], {"p1b01": "Hello synthetic report."}, style(), {}, {}, {},
                          tmp_path, None, None,
                          {"title": "Synthetic", "author": "Acme", "run_id": "run-7"})
    assert Path(result["pdf"]).exists() and Path(result["pngs"][0]).exists()
    assert result["pages"] == 1
    with pymupdf.open(result["pdf"]) as document:
        assert document.page_count == 1 and document.metadata["title"] == "Synthetic"
        assert "decraft" not in document.get_xml_metadata().lower() and "run-7" not in document.get_xml_metadata()


def test_rendered_tables_never_show_source_wording_or_csv_artifacts():
    """Live run 2026-10-26: unwritten boxes printed the SOURCE paragraph; tables showed 'Unnamed: 1'/'nan'."""
    import pandas as pd
    from src.rendering._pages import block_html
    text = Block(block_id="p1b05", kind="text", bbox=(0, 0, 100, 40), text="EBITDA surged 76.0% YoY to Rs. 3,215cr")
    assert "3,215" not in block_html(text, style(), {}, {}, {}, {}, 1)
    table = Block(block_id="p1b06", kind="table", bbox=(0, 0, 300, 100), table_id="t")
    frame = pd.DataFrame({"Company Data": ["Market Cap (cr)"], "Unnamed: 1": [float("nan")], "Unnamed: 2": ["Rs. 9"]})
    html = block_html(table, style(), {}, {"t": frame}, {}, {}, 1)
    assert "Unnamed" not in html and "nan" not in html and "Rs. 9" in html


def test_page_sized_decoration_block_does_not_hide_bands(tmp_path):
    """Live run 2026-10-26: a full-page 'decoration' block swallowed page 1's masthead and footer bands."""
    from src.rendering._pages import decorations
    path = tmp_path / "bands.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(0, 63, 595, 185), color=None, fill=(0, 0.5, 0.5))    # masthead band
    page.draw_rect(pymupdf.Rect(0, 790, 595, 842), color=None, fill=(0, 0.5, 0.5))   # footer band
    page.draw_rect(pymupdf.Rect(300, 500, 312, 560), color=(0, 0, 1), fill=(0, 0, 1))  # chart bar
    document.save(str(path))
    document.close()
    avoid = [Block(block_id="bg", kind="decoration", bbox=(-15, 1, 598, 840)),
             Block(block_id="c", kind="chart", bbox=(290, 480, 330, 570))]
    svg = decorations(str(path), 1, 595, 842, avoid)
    assert svg.count("<rect") == 2                       # both bands, never the chart bar


def test_flow_page_keeps_bands_but_not_body_panels(tmp_path):
    """Flow bodies reflow, so a source panel there would sit behind the wrong content."""
    from src.rendering._pages import decorations
    path = tmp_path / "panel.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(0, 63, 595, 185), color=None, fill=(0, 0.5, 0.5))     # masthead band
    page.draw_rect(pymupdf.Rect(36, 197, 362, 381), color=None, fill=(0.8, 0.8, 0.8))  # summary panel
    document.save(str(path))
    document.close()
    assert decorations(str(path), 1, 595, 842, []).count("<rect") == 2           # mirror: both
    assert decorations(str(path), 1, 595, 842, [], body=(185, 790)).count("<rect") == 1


def test_band_text_gets_readable_ink():
    """Live run 2026-10-26: the teal title vanished on the teal masthead."""
    from src.rendering._pages import _ink
    teal = style().model_copy(update={"text_styles": [TextStyle(
        style_id="T1", font_id="F1", size_pt=10, color="#078777", line_height=1.2, align="left")]})
    on_band = Block(block_id="t", kind="heading", bbox=(50, 100, 300, 120), style_id="T1")
    below = Block(block_id="u", kind="heading", bbox=(50, 300, 300, 320), style_id="T1")
    fills = [((0, 63, 595, 185), "#078777")]
    assert _ink(on_band, teal, fills) == "ink-light"
    assert _ink(below, teal, fills) == ""
    assert _ink(on_band, style(), [((0, 63, 595, 185), "#FFFFFF")]) == ""    # dark on white is fine


def test_filled_line_outline_becomes_one_filled_path(tmp_path):
    """Live run 2026-10-26: the masthead's slanted teal shape (four filled line segments) vanished."""
    from src.rendering._pages import decorations
    path = tmp_path / "slant.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    shape = page.new_shape()
    shape.draw_polyline([(0, 1), (411, 1), (380, 33), (0, 33), (0, 1)])
    shape.finish(color=None, fill=(0, 0.5, 0.5), closePath=True)
    shape.commit()
    document.save(str(path))
    document.close()
    svg = decorations(str(path), 1, 595, 842, [])
    assert svg.count("<path") == 1 and "<line" not in svg and "fill='#00" in svg


def test_no_website_is_ever_rendered():
    """User rule 2026-10-26: no website, e-mail or domain appears anywhere in the output."""
    import pandas as pd
    from src.rendering._pages import block_html, strip_websites
    footer = Block(block_id="f", kind="footer", bbox=(0, 380, 200, 400), text="www.geojit.com")
    assert "geojit" not in block_html(footer, style(), {}, {}, {}, {}, 1)
    text = Block(block_id="t", kind="text", bbox=(0, 0, 100, 40))
    html = block_html(text, style(), {"t": "See https://acme.example.com/r (report). Mail ir@acme.co.in."},
                      {}, {}, {}, 1)
    assert "http" not in html and "@" not in html and ".co" not in html and "See" in html
    table = Block(block_id="tb", kind="table", bbox=(0, 0, 300, 100), table_id="x")
    frame = pd.DataFrame({"Item": ["Web"], "Value": ["www.acme.com"]})
    assert "acme" not in block_html(table, style(), {}, {"x": frame}, {}, {}, 1)
    for kept in ("Rs. 2.5 cr in FY24.", "Adj. EPS (Rs.)", "Tata Power Co. Ltd.", "We contact the board."):
        assert strip_websites(kept) == kept
