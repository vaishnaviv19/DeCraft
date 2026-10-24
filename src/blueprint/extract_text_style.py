# src/blueprint/extract_text_style.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import fitz  # pymupdf


def _hex_from_color(color_int: int) -> str:
    if color_int is None:
        return "#000000"
    try:
        if isinstance(color_int, int):
            r = (color_int >> 16) & 0xFF
            g = (color_int >> 8) & 0xFF
            b = color_int & 0xFF
            return f"#{r:02X}{g:02X}{b:02X}"
        return str(color_int)
    except Exception:
        return "#000000"


def extract_text_blocks(pdf_path: str | Path) -> List[Dict[str, Any]]:
    """
    FR-A2: Extract text blocks with font name, size, weight, colour, and bounding box per page.
    """
    doc = fitz.open(str(pdf_path))
    blocks_out: List[Dict[str, Any]] = []

    for page_num, page in enumerate(doc, start=1):
        data = page.get_text("dict")
        for b_idx, block in enumerate(data.get("blocks", [])):
            if block.get("type") != 0:
                continue
            for l_idx, line in enumerate(block.get("lines", [])):
                for s_idx, span in enumerate(line.get("spans", [])):
                    text = span.get("text", "")
                    if not text.strip():
                        continue
                    font = span.get("font", "")
                    size = span.get("size", 0.0)
                    flags = span.get("flags", 0)
                    is_bold = bool(flags & (1 << 4)) or "bold" in str(font).lower() or "black" in str(font).lower()
                    color_int = span.get("color", 0)
                    bbox = span.get("bbox", [0, 0, 0, 0])
                    origin = span.get("origin", [0, 0])
                    blocks_out.append(
                        {
                            "page": page_num,
                            "block_id": f"p{page_num}_b{b_idx}_l{l_idx}_s{s_idx}",
                            "text": text,
                            "font_name": font,
                            "font_size": round(float(size), 2),
                            "font_weight": "bold" if is_bold else "normal",
                            "is_bold": bool(is_bold),
                            "color": _hex_from_color(color_int),
                            "bbox": {
                                "x0": round(float(bbox[0]), 2),
                                "y0": round(float(bbox[1]), 2),
                                "x1": round(float(bbox[2]), 2),
                                "y1": round(float(bbox[3]), 2),
                            },
                            "origin": {
                                "x": round(float(origin[0]), 2),
                                "y": round(float(origin[1]), 2),
                            },
                        }
                    )
    doc.close()
    return blocks_out
