# src/rendering/_measure.py
"""Text measurement (M8.9.1): advance widths from the font's own `hmtx` table.

`width_fn(text)` returns points for the block's font at the block's Text Style size, with a 2%
safety margin and kerning ignored (E-13). An extracted font file is used when it exists; otherwise
the metric-compatible fallback is looked up, and DejaVu Sans (shipped with matplotlib, so always on
D:) is the last resort."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from src.contracts.report import Block, FontSpec, StyleProfile, TextStyle
from src.rendering._fit import Box

SAFETY = 1.02


@lru_cache(maxsize=64)
def _advance_table(path: str) -> tuple[dict[str, int], int, int]:
    from fontTools.ttLib import TTFont

    # read everything, then CLOSE the file: a TTFont left open keeps the font locked on Windows and the
    # next run could not rewrite style/fonts/*.ttf (WinError 32)
    with TTFont(path, fontNumber=0, lazy=False) as font:
        units = font["head"].unitsPerEm
        metrics = font["hmtx"].metrics
        table: dict[str, int] = {}
        for code, glyph in (font.getBestCmap() or {}).items():
            if glyph in metrics:
                table[chr(code)] = metrics[glyph][0]
    return table, units, table.get("n", units // 2)


def advance_width_fn(font_path: str, size_pt: float, safety: float = SAFETY):
    """Points for a string in this font at this size (no kerning), times the safety margin."""
    table, units, default = _advance_table(font_path)

    def width(text: str) -> float:
        return sum(table.get(char, default) for char in text) * size_pt / units * safety

    return width


def synthetic_width_fn(size_pt: float, safety: float = SAFETY):
    """Fallback when no font file can be opened: half an em per character."""
    def width(text: str) -> float:
        return len(text) * size_pt * 0.5 * safety

    return width


def _bundled_font(bold: bool) -> str | None:
    try:
        import matplotlib
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        path = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf" / name
        return str(path) if path.exists() else None
    except Exception:
        return None


def _system_font(family: str, bold: bool) -> str | None:
    try:
        from matplotlib import font_manager
        found = font_manager.findfont(font_manager.FontProperties(
            family=family, weight="bold" if bold else "normal"), fallback_to_default=False)
        return found if found and Path(found).exists() else None
    except Exception:
        return None


def font_path(font: FontSpec | None, artifacts_root: Path | None = None) -> str | None:
    """The extracted file first, then the fallback family, then DejaVu Sans."""
    if font is not None:
        if font.file:
            candidate = Path(font.file)
            roots = [candidate] if candidate.is_absolute() else []
            if artifacts_root is not None:
                roots.append(Path(artifacts_root) / candidate)
                roots.append(Path(artifacts_root) / "runs" / "dev" / "style" / "fonts" / candidate.name)
            for path in roots:
                if path.exists():
                    return str(path)
        found = _system_font(font.family or font.fallback, font.bold) \
            or _system_font(font.fallback, font.bold)
        if found:
            return found
    return _bundled_font(font.bold if font is not None else False)


def text_style_for(style: StyleProfile, block: Block) -> TextStyle:
    for text_style in style.text_styles:
        if text_style.style_id == block.style_id:
            return text_style
    return TextStyle(style_id="fallback", font_id="F1", size_pt=10.0, color="#000000",
                     line_height=1.2)


def font_for(style: StyleProfile, text_style: TextStyle) -> FontSpec | None:
    for font in style.fonts:
        if font.font_id == text_style.font_id:
            return font
    return style.fonts[0] if style.fonts else None


def box_for(block: Block, style: StyleProfile) -> Box:
    x0, y0, x1, y1 = block.bbox
    text_style = text_style_for(style, block)
    return Box(width_pt=max(1.0, x1 - x0), height_pt=max(1.0, y1 - y0),
               font_size=text_style.size_pt, line_height=text_style.line_height)


def width_fn_for(block: Block, style: StyleProfile, artifacts_root: Path | None = None):
    text_style = text_style_for(style, block)
    path = font_path(font_for(style, text_style), artifacts_root)
    return advance_width_fn(path, text_style.size_pt) if path \
        else synthetic_width_fn(text_style.size_pt)
