# src/rendering/_fit.py
"""Does a text fit a mirrored box? (decision E-14). Greedy word wrap with real advance widths.
`width_fn(text) -> points` comes from the font (fontTools hmtx) in production; tests inject a fake."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    width_pt: float
    height_pt: float
    font_size: float
    line_height: float          # multiple of font size, from the source block (e.g. 1.25)


def wrap_lines(text: str, box: Box, width_fn) -> int:
    lines = 0
    for para in [p for p in text.split("\n\n") if p.strip()]:
        cur = ""
        lines += 1
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if width_fn(trial) <= box.width_pt or not cur:
                cur = trial
            else:
                lines += 1
                cur = word
    return lines


def capacity_lines(box: Box) -> int:
    # A non-empty box always has room for at least one line. Source one-line boxes are only a glyph
    # bbox tall (e.g. 10.5 pt for 9 pt text), so a raw floor would yield zero and cut the block away.
    return max(1, int(box.height_pt // (box.font_size * box.line_height)))


def fits(text: str, box: Box, width_fn) -> tuple[bool, int, int]:
    need, have = wrap_lines(text, box, width_fn), capacity_lines(box)
    return need <= have, need, have


def word_budget(source_words: int, tolerance: float = 0.15) -> tuple[int, int]:
    """Per-box word range from the SOURCE box's word count (E-13): -15% up to the source count itself
    (the maximum is a hard limit given to the writer; the source text fits its box)."""
    return max(1, int(source_words * (1 - tolerance))), max(1, int(source_words))
