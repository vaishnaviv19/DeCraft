

def test_vector_charts_become_chart_blocks_but_icons_and_shading_do_not(tmp_path):
    """Live run 2026-10-26: no chart block was ever made, so no chart reached the output."""
    import pymupdf
    from src.style._templates import chart_blocks
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    shape = page.new_shape()                                       # a price line (40 segments)
    shape.draw_polyline([(100 + 5 * i, 300 - (i * 7) % 40) for i in range(41)])
    shape.finish(color=(0, 0.5, 0.5))
    shape.commit()
    for i, h in enumerate([20, 45, 30, 60, 50]):                   # five bars on one baseline
        page.draw_rect(pymupdf.Rect(100 + 20 * i, 600 - h, 112 + 20 * i, 600), color=None, fill=(0, 0, 1))
    for i in range(6):                                             # table shading: equal heights
        page.draw_rect(pymupdf.Rect(350 + 30 * i, 600, 375 + 30 * i, 612), color=None, fill=(0.9, 0.9, 0.9))
    icon = page.new_shape()                                        # a 12 pt arrow icon
    icon.draw_polyline([(500, 100), (506, 94), (512, 100), (509, 100), (509, 108), (503, 108), (503, 100), (500, 100)])
    icon.finish(color=(0, 0, 0))
    icon.commit()
    document.save(str(tmp_path / "charts.pdf"))
    document.close()
    with pymupdf.open(str(tmp_path / "charts.pdf")) as pdf:
        boxes = sorted(tuple(round(v) for v in block.bbox) for block in chart_blocks(pdf[0]))
    assert len(boxes) == 2
    line, bars = boxes
    assert line[0] == 100 and line[2] == 300                       # the line chart
    assert bars[0] == 100 and bars[3] == 600                       # the bars, not the shading


def test_a_chart_line_running_off_its_clip_is_boxed_by_what_is_visible(tmp_path):
    import pymupdf
    from src.style._templates import chart_blocks
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    # draw a long line clipped to (100, 100, 300, 200) through a content stream
    points = " ".join(f"{-200 + 10 * i} {735 - (i * 13) % 60} l" for i in range(1, 50))
    stream = f"q 100 642 200 100 re W n 0 0 1 RG -200 735 m {points} S Q".encode()
    xref = document.get_new_xref()
    document.update_object(xref, "<<>>")
    document.update_stream(xref, stream)
    page.set_contents(xref)
    document.save(str(tmp_path / "clip.pdf"))
    document.close()
    with pymupdf.open(str(tmp_path / "clip.pdf")) as pdf:
        boxes = [block.bbox for block in chart_blocks(pdf[0])]
    assert len(boxes) == 1 and boxes[0][0] >= 99 and boxes[0][2] <= 301
