# src/style/_ocr.py   (v3.3 F-04: scanned pages)
"""A page needs OCR when it has almost no extractable text but is mostly covered by images.
OCR engine: RapidOCR (ONNX, CPU, pip-installable). Its import is inside run_ocr so the rest works without it."""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf


@dataclass(frozen=True)
class OcrSpan:
    text: str
    bbox: tuple[float, float, float, float]      # PDF points
    confidence: float


@dataclass
class OcrResult:
    spans: list[OcrSpan]
    dropped: int                                 # spans below the confidence floor (reported, F-04)


def page_needs_ocr(page: "pymupdf.Page", min_chars: int = 50, min_image_cover: float = 0.5) -> bool:
    chars = len(page.get_text("text").strip())
    if chars >= min_chars:
        return False
    area = page.rect.width * page.rect.height
    covered = 0.0
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            covered += (r & page.rect).get_area()
    return area > 0 and covered / area >= min_image_cover


def ocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


_ENGINE = None


def run_ocr(page: "pymupdf.Page", dpi: int = 200, min_conf: float = 0.6) -> OcrResult:
    """Render the page, OCR it, map pixel boxes back to points. Spans below min_conf are dropped and counted."""
    global _ENGINE
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    if _ENGINE is None:
        _ENGINE = RapidOCR()
    pix = page.get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    result, _ = _ENGINE(img)
    k = 72.0 / dpi
    spans, dropped = [], 0
    for box, text, conf in result or []:
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        if float(conf) >= min_conf and str(text).strip():
            spans.append(OcrSpan(str(text).strip(), (min(xs) * k, min(ys) * k, max(xs) * k, max(ys) * k),
                                 float(conf)))
        else:
            dropped += 1
    return OcrResult(spans, dropped)


def add_text_layer(page: "pymupdf.Page", spans: list[OcrSpan]) -> None:
    """Write the OCR text INVISIBLY onto the page at its boxes (render mode 3), so every text-based step
    (style, templates, extraction) reads a scanned page like a born-digital one."""
    for span in spans:
        x0, y0, x1, y1 = span.bbox
        size = max(4.0, (y1 - y0) * 0.8)
        page.insert_text((x0, y1 - 0.2 * (y1 - y0)), span.text, fontsize=size, render_mode=3)


def ocr_copy(pdf_path, out_path, min_chars: int = 50, min_image_cover: float = 0.5,
             min_conf: float = 0.6) -> dict:
    """A copy of the PDF where every scanned page has an invisible OCR text layer. Returns
    {"ocr_pages": [...], "dropped_spans": n, "engine": bool, "path": str}. Without the engine, or when no
    page needs OCR, the source path is returned unchanged."""
    with pymupdf.open(str(pdf_path)) as document:
        pages = [i for i in range(document.page_count)
                 if page_needs_ocr(document[i], min_chars, min_image_cover)]
        if not pages or not ocr_available():
            return {"ocr_pages": [p + 1 for p in pages], "dropped_spans": 0,
                    "engine": ocr_available(), "path": str(pdf_path), "applied": False}
        dropped = 0
        for i in pages:
            result = run_ocr(document[i], min_conf=min_conf)       # re-fetch: never hold pages across edits
            add_text_layer(document[i], result.spans)
            dropped += result.dropped
        document.save(str(out_path))
    return {"ocr_pages": [p + 1 for p in pages], "dropped_spans": dropped, "engine": True,
            "path": str(out_path), "applied": True}
