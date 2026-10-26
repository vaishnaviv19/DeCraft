# tests/style/test_v33_ocr_layout.py  -- v3.3 F-04 OCR trigger, F-05 layout agreement
import pymupdf
import pytest

from src.style._layout_check import agreement
from src.style._ocr import ocr_available, ocr_copy, page_needs_ocr, run_ocr


def test_scanned_page_needs_ocr_text_page_does_not():
    doc = pymupdf.open()
    p1 = doc.new_page(width=300, height=300)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
    pix.clear_with(200)
    p1.insert_image(p1.rect, pixmap=pix)
    doc.new_page(width=300, height=300)
    doc[1].insert_text((20, 40), "A normal text page with plenty of characters to extract. " * 2, fontsize=8)
    # PyMuPDF: adding a page invalidates page objects already held -> always re-fetch with doc[i]
    assert page_needs_ocr(doc[0]) and not page_needs_ocr(doc[1])


def test_layout_agreement():
    ours = [("heading", (50, 40, 300, 60)), ("text", (50, 70, 550, 200)), ("table", (50, 220, 550, 400)),
            ("footer", (50, 800, 550, 820))]
    model = [("title", (52, 41, 298, 61), 0.9), ("plain text", (50, 72, 548, 198), 0.8),
             ("figure", (50, 220, 550, 400), 0.7)]
    share, issues = agreement(ours, model)
    assert share == 2 / 3 and "table" in issues[0]


@pytest.mark.slow
@pytest.mark.skipif(not ocr_available(), reason="rapidocr_onnxruntime not installed")
def test_ocr_recovers_most_words_of_an_image_only_page(tmp_path):
    words = "Quarterly revenue increased across every region while operating costs remained stable".split()
    text_doc = pymupdf.open()
    page = text_doc.new_page(width=500, height=200)
    page.insert_text((30, 80), " ".join(words), fontsize=12)
    pix = page.get_pixmap(dpi=200)
    scanned = pymupdf.open()
    scan_page = scanned.new_page(width=500, height=200)
    scan_page.insert_image(scan_page.rect, pixmap=pix)
    path = tmp_path / "scan.pdf"
    scanned.save(str(path))
    with pymupdf.open(str(path)) as document:
        found = " ".join(span.text for span in run_ocr(document[0]).spans).lower()
    recovered = sum(1 for w in words if w.lower() in found) / len(words)
    print(f"OCR word recovery: {recovered:.0%}")
    assert recovered >= 0.8
    info = ocr_copy(path, tmp_path / "scan_ocr.pdf")
    assert info["applied"] and info["ocr_pages"] == [1]
    with pymupdf.open(info["path"]) as document:
        assert "revenue" in document[0].get_text().lower()
