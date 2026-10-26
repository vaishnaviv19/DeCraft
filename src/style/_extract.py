# src/style/_extract.py
"""Style extraction (M8.1): fonts, Text Styles, palette, page geometry, running header/footer.

Everything here is measured from the Source PDF with PyMuPDF; nothing is assumed about the domain
(E-25). Embedded font files are reused when they are complete (E-21); otherwise a metric-compatible
open font is named as the fallback.
"""
from __future__ import annotations

import io
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

from src.config import Config
from src.contracts.report import FontSpec, StyleProfile, TextStyle

FALLBACKS = {
    "calibri": "Carlito", "cambria": "Caladea",
    "arial": "Liberation Sans", "helvetica": "Liberation Sans",
    "times": "Liberation Serif", "times new roman": "Liberation Serif",
    "courier": "Liberation Mono", "courier new": "Liberation Mono",
    "segoe ui": "Open Sans", "open sans": "Open Sans", "roboto": "Open Sans",
    "georgia": "Gelasio", "garamond": "EB Garamond",
}
SERIF_HINTS = ("serif", "times", "georgia", "garamond", "cambria", "baskerville", "palatino", "book")
MIN_GLYPHS = 200


FAMILY_ALIASES = {
    "arial": "Arial", "helvetica": "Helvetica", "times": "Times", "timesnewroman": "Times New Roman",
    "calibri": "Calibri", "cambria": "Cambria", "georgia": "Georgia", "verdana": "Verdana",
    "tahoma": "Tahoma", "trebuchet": "Trebuchet MS", "trebuchetms": "Trebuchet MS",
    "garamond": "Garamond", "courier": "Courier", "couriernew": "Courier New",
    "segoeui": "Segoe UI", "opensans": "Open Sans", "roboto": "Roboto", "futura": "Futura",
    "centurygothic": "Century Gothic", "palatino": "Palatino", "bookantiqua": "Book Antiqua",
    "baskerville": "Baskerville", "candara": "Candara", "constantia": "Constantia",
    "corbel": "Corbel", "gillsans": "Gill Sans", "gillsansmt": "Gill Sans",
}
STYLE_TOKENS = ("bold", "black", "heavy", "semibold", "demibold", "italic", "oblique", "regular",
                "roman", "book", "narrow", "light", "medium", "mt", "psmt", "std", "pro", "normal")


def _alias(family: str) -> str | None:
    return FAMILY_ALIASES.get(family.lower().replace(" ", "").replace("-", ""))


def clean_font_name(source_name: str) -> tuple[str, bool, bool]:
    """'ABCDEF+Calibri-Bold' -> ('Calibri', bold=True, italic=False)."""
    name = re.sub(r"^[A-Z]{6}\+", "", str(source_name or ""))
    bold = bool(re.search(r"bold|black|heavy|semibold|demibold", name, re.I))
    italic = bool(re.search(r"italic|oblique", name, re.I))
    family = name
    changed = True
    while changed and family:
        family = family.strip(" -_,")
        if _alias(family):
            break                                   # a known family name: never strip inside it
        low = family.lower()
        for token in STYLE_TOKENS:
            if low.endswith(token) and len(family) > len(token):
                family = family[: -len(token)]
                changed = True
                break
        else:
            changed = False
    family = _alias(family) or family.strip(" -_,") or "Unknown"
    return family, bold, italic


def fallback_for(family: str) -> str:
    key = (family or "").strip().lower()
    if key in FALLBACKS:
        return FALLBACKS[key]
    for name, fallback in FALLBACKS.items():
        if name in key:
            return fallback
    return "Liberation Serif" if any(h in key for h in SERIF_HINTS) else "Liberation Sans"


def _hex(color) -> str:
    if color is None:
        return "#000000"
    if isinstance(color, str):
        return color.upper() if color.startswith("#") else "#" + color.upper()
    if isinstance(color, int):
        return f"#{color >> 16 & 255:02X}{color >> 8 & 255:02X}{color & 255:02X}"
    r, g, b = (max(0, min(255, round(float(c) * 255))) for c in tuple(color)[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def _near(color: str, threshold: int = 245) -> bool:
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    return r > threshold and g > threshold and b > threshold


def _near_black(color: str, threshold: int = 18) -> bool:
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    return r < threshold and g < threshold and b < threshold


def _extract_font(doc, xref: int, ext: str) -> bytes | None:
    """A complete TrueType/OpenType/CFF file, or None for a subset/unusable one (E-21)."""
    if ext not in {"ttf", "otf", "cff"}:
        return None
    try:
        _name, _ext, _type, buffer = doc.extract_font(xref)
    except Exception:
        return None
    if not buffer:
        return None
    try:
        from fontTools.ttLib import TTFont
        font = TTFont(io.BytesIO(buffer))
        if len(font.getGlyphOrder()) <= MIN_GLYPHS:
            return None
    except Exception:
        return None
    return buffer


def _page_text_spans(page):
    data = page.get_text("dict")
    for block_no, block in enumerate(data.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line_no, line in enumerate(block.get("lines", [])):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if text.strip():
                    yield block_no, line_no, span


def extract_style(pdf_path: Path, cfg: Config) -> StyleProfile:
    """Measure the source's look: fonts, Text Styles, palette, page size, margins, header/footer."""
    with pymupdf.open(str(pdf_path)) as doc:
        if len(doc) == 0:
            raise ValueError("the PDF has no pages")
        first = doc[0]
        page_width, page_height = float(first.rect.width), float(first.rect.height)

        fonts: dict[tuple[str, bool, bool], FontSpec] = {}
        style_stats: dict[tuple, dict] = defaultdict(lambda: {"chars": 0, "gap": [], "blocks": [], "source": ""})
        palette_counts: Counter = Counter()
        big_by_area: Counter = Counter()
        header_pages: dict[str, set[int]] = defaultdict(set)
        footer_pages: dict[str, set[int]] = defaultdict(set)
        page1_top: list[str] = []
        text_boxes: list[tuple[float, float, float, float]] = []
        widths: list[float] = []

        fonts_dir = Path(cfg.artifacts_dir) / "runs" / "dev" / "style" / "fonts"
        embedded: dict[tuple[str, bool, bool], bytes] = {}
        for page_no, page in enumerate(doc, start=1):
            height = float(page.rect.height)
            for _xref, ext, _kind, base_font, *_rest in page.get_fonts(full=True):
                family, bold, italic = clean_font_name(base_font)
                key = (family, bold, italic)
                if key in embedded:
                    continue
                buffer = _extract_font(doc, int(_xref), str(ext))
                if buffer is not None:
                    embedded[key] = buffer

            for block_no, line_no, span in _page_text_spans(page):
                text = span.get("text", "")
                size = round(float(span.get("size", 10.0)) * 2) / 2
                color = _hex(span.get("color", 0))
                family, bold, italic = clean_font_name(span.get("font", ""))
                flags = int(span.get("flags", 0))
                bold = bold or bool(flags & 16)
                italic = italic or bool(flags & 2)
                key = (family, bold, italic)
                fonts.setdefault(key, FontSpec(font_id=f"F{len(fonts) + 1}",
                                               source_name=str(span.get("font", "")),
                                               family=family, bold=bold, italic=italic,
                                               fallback=fallback_for(family)))
                style_key = (family, bold, italic, size, color)
                stats = style_stats[style_key]
                stats["chars"] += len(text)
                stats["source"] = str(span.get("font", ""))
                stats["blocks"].append((block_no, line_no, float(span["bbox"][0]),
                                        float(span["bbox"][1]), float(span["bbox"][2]),
                                        float(span["bbox"][3])))
                stats["gap"].append((block_no, line_no, float(span["origin"][1])))
                palette_counts[color] += len(text)

                x0, y0, x1, y1 = (float(v) for v in span["bbox"])
                compact = re.sub(r"\s+", " ", text).strip()
                if y1 <= 0.08 * height:
                    header_pages[compact].add(page_no)
                    if page_no == 1:
                        page1_top.append(compact)
                elif y0 >= 0.92 * height:
                    footer_pages[compact].add(page_no)
                else:
                    text_boxes.append((x0, y0, x1, y1))

            for drawing in page.get_drawings():
                rect = drawing.get("rect")
                if rect is None:
                    continue
                area = float(rect.width * rect.height)
                for colour in (drawing.get("fill"), drawing.get("color")):
                    if colour is not None:
                        hexed = _hex(colour)
                        big_by_area[hexed] += max(area, 1.0) / 100.0
                if drawing.get("width"):
                    widths.append(float(drawing["width"]))

        # Fonts: save complete embedded files; the rest keep their fallback name.
        for key, spec in fonts.items():
            if key in embedded:
                fonts_dir.mkdir(parents=True, exist_ok=True)
                suffix = "otf" if spec.family.lower() in {"cff"} else "ttf"
                target = fonts_dir / f"{spec.font_id}-{re.sub(r'[^A-Za-z0-9]', '_', spec.family)}.{suffix}"
                target.write_bytes(embedded[key])
                spec.file = target.relative_to(Path(cfg.artifacts_dir)).as_posix()

        # Text Styles, most used first.
        ordered = sorted(style_stats.items(), key=lambda item: -item[1]["chars"])
        body_color = ordered[0][0][4] if ordered else "#000000"
        text_styles: list[TextStyle] = []
        for n, (key, stats) in enumerate(ordered, start=1):
            family, bold, italic, size, color = key
            font_key = (family, bold, italic)
            gaps = []
            by_block: dict[int, list[tuple[int, float]]] = defaultdict(list)
            for block_no, line_no, baseline in stats["gap"]:
                by_block[block_no].append((line_no, baseline))
            for lines in by_block.values():
                lines.sort()
                baselines = [b for _, b in lines]
                gaps.extend(b - a for a, b in zip(baselines, baselines[1:]) if b > a)
            line_height = round(statistics.median(gaps) / size, 2) if gaps and size else 1.2
            text_styles.append(TextStyle(
                style_id=f"T{n}", font_id=fonts[font_key].font_id, size_pt=size, color=color,
                line_height=max(0.8, min(3.0, line_height)), align="left"))
        text_styles = text_styles[:25]          # the long tail of odd colours is noise, not a style

        # Palette: text colours plus drawing fills/strokes; near-white always dropped, near-black only
        # when the body text is black.
        palette: Counter = Counter()
        for colour, weight in palette_counts.items():
            if _near(colour):
                continue
            if _near_black(colour) and _near_black(body_color):
                continue
            palette[colour] += weight
        for colour, weight in big_by_area.items():
            if _near(colour):
                continue
            if _near_black(colour) and _near_black(body_color):
                continue
            palette[colour] += weight
        colours = [c for c, _ in palette.most_common(8)]
        if not colours:
            colours = [body_color if not _near(body_color) else "#333333"]

        pages = len(doc)
        threshold = max(1, int(round(0.6 * pages)))
        header = next((text for text, found in sorted(header_pages.items(), key=lambda kv: -len(kv[1]))
                       if len(found) >= threshold), None)
        if header is None and page1_top:
            header = " ".join(dict.fromkeys(page1_top))[:160]
        footer = next((text for text, found in sorted(footer_pages.items(), key=lambda kv: -len(kv[1]))
                       if len(found) >= threshold), None)

        if text_boxes:
            left = max(0.0, min(b[0] for b in text_boxes))
            top = max(0.0, min(b[1] for b in text_boxes))
            right = max(0.0, page_width - max(b[2] for b in text_boxes))
            bottom = max(0.0, page_height - max(b[3] for b in text_boxes))
        else:
            left, top, right, bottom = 36.0, 36.0, 36.0, 36.0

        return StyleProfile(
            page_width_pt=page_width, page_height_pt=page_height,
            margins_pt=(round(top, 2), round(right, 2), round(bottom, 2), round(left, 2)),
            fonts=list(fonts.values()), text_styles=text_styles, palette=[c.upper() for c in colours],
            table_border_pt=round(statistics.median(widths), 2) if widths else 0.5,
            header_text=header, footer_text=footer)
