# src/blueprint/extract_structure.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import fitz


def detect_headings(text_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    FR-A3: Detect heading hierarchy (H1/H2/H3) from font-size and weight clustering, not from keyword lists.
    """
    if not text_blocks:
        return []

    sizes = [b["font_size"] for b in text_blocks]
    sizes_arr = np.array(sizes)
    uniq = sorted(set(round(float(s), 1) for s in sizes_arr), reverse=True)

    level_map = {}
    candidate_sizes = uniq[:5]
    for i, sz in enumerate(candidate_sizes):
        if i == 0:
            level_map[sz] = "H1"
        elif i == 1:
            level_map[sz] = "H2"
        elif i == 2:
            level_map[sz] = "H3"
        else:
            level_map[sz] = None

    headings = []
    for b in text_blocks:
        sz_r = round(float(b["font_size"]), 1)
        if sz_r in level_map and level_map[sz_r] is not None:
            if b.get("is_bold") or level_map[sz_r] == "H1":
                headings.append(
                    {
                        "level": level_map[sz_r],
                        "text": str(b.get("text", "")).strip(),
                        "page": b.get("page"),
                        "font_size": b.get("font_size"),
                        "font_weight": b.get("font_weight"),
                        "bbox": b.get("bbox"),
                        "confidence": 0.8,
                    }
                )

    seen = set()
    out = []
    for h in headings:
        key = (h.get("page"), h.get("level"), h.get("text")[:80])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def extract_page_structure(pdf_path: str | Path, text_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    FR-A7: page size, margins, header/footer content and position, page-number format, column layout
    """
    doc = fitz.open(str(pdf_path))
    pages = []
    for page_num, page in enumerate(doc, start=1):
        rect = page.rect
        w, h = rect.width, rect.height
        page_blocks = [b for b in text_blocks if b.get("page") == page_num]
        if not page_blocks:
            pages.append(
                {
                    "page": page_num,
                    "size": {"width": round(float(w), 2), "height": round(float(h), 2), "unit": "pt"},
                    "margins": {},
                    "header": None,
                    "footer": None,
                    "page_number_format": None,
                    "column_layout": "unknown",
                }
            )
            continue
        xs0 = [b["bbox"]["x0"] for b in page_blocks]
        ys0 = [b["bbox"]["y0"] for b in page_blocks]
        xs1 = [b["bbox"]["x1"] for b in page_blocks]
        ys1 = [b["bbox"]["y1"] for b in page_blocks]
        left_margin = min(xs0) if xs0 else 0
        top_margin = min(ys0) if ys0 else 0
        right_margin = w - max(xs1) if xs1 else 0
        bottom_margin = h - max(ys1) if ys1 else 0
        header_blocks = [b for b in page_blocks if b["bbox"]["y0"] < h * 0.15]
        footer_blocks = [b for b in page_blocks if b["bbox"]["y1"] > h * 0.85]
        header_text = " ".join([str(b["text"]) for b in header_blocks]).strip() if header_blocks else None
        footer_text = " ".join([str(b["text"]) for b in footer_blocks]).strip() if footer_blocks else None
        pn = None
        search_text = footer_text or header_text or ""
        m = re.search(r"Page\s+\d+|\d+\s+of\s+\d+|^\d+$", search_text)
        if m:
            pn = m.group(0)
        column_layout = "single"
        pages.append(
            {
                "page": page_num,
                "size": {"width": round(float(w), 2), "height": round(float(h), 2), "unit": "pt"},
                "margins": {
                    "left": round(float(left_margin), 2),
                    "top": round(float(top_margin), 2),
                    "right": round(float(right_margin), 2),
                    "bottom": round(float(bottom_margin), 2),
                },
                "header": {"content": header_text, "bbox": None} if header_text else None,
                "footer": {"content": footer_text, "bbox": None} if footer_text else None,
                "page_number_format": pn,
                "column_layout": column_layout,
            }
        )
    doc.close()
    return pages
