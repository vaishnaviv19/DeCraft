# src/rendering/_masthead.py  -- E-55: the masthead is branding, its figures are fabricated
"""Boxes inside the source's top band (report series, company name, sector, rating, the price box,
listing codes, the 'key changes' strip) are the publisher's and the organisation's branding and field
labels, not prose: a writer filled them with sentences and spare headings ('Key' for 'BUY').

Their wording stays; what they say is new:
* a number equal to a table cell of the source takes that cell's NEW value (TARGET Rs. 301 -> the
  fabricated SOTP target price), so the masthead agrees with the tables;
* any other figure (a price, an index level, a percentage) moves by an independent safe factor;
* a percentage that is the change between two figures of the same box (RETURN 11% from CMP and
  TARGET) is recomputed from their new values;
* listing codes stay real (E-41), dates and periods move forward (E-16), small counts in wording
  ('12 Months') stay.
"""
from __future__ import annotations

import re

import numpy as np

from src.core.periods import shift_text_periods
from src.core.values import format_number_like, parse_number

CODE_RE = re.compile(r"\b(?:CODE|Code|code|Ticker|Symbol|ISIN|RIC)\b\s*:\s*[A-Z0-9][A-Z0-9.:\-]*")
DATE_RE = re.compile(r"\b(\d{1,2})([-/.])(\d{1,2})\2(\d{2}|\d{4})\b")
ORDINAL_RE = re.compile(r"\b(\d{1,2})\s+(st|nd|rd|th)\b")
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9.])(\(?)((?:rs\.?|inr|usd|[$€£₹])\s*)?([-+]?\d[\d,]*(?:\.\d+)?)(\s*%)?(\)?)(?![A-Za-z0-9])",
                       re.I)


def number_map(grids: dict, display: dict) -> dict[float, float]:
    """Source value -> its new printed value, for table values with 3+ significant digits. A value printed
    in several cells maps when every cell got the same new value, or when the cells are one column of
    one table (a dated history repeating a target): its newest - bottom - row decides."""
    found: dict[float, list] = {}
    for table_id, grid in grids.items():
        frame = display.get(table_id)
        if frame is None or len(frame) != len(grid.raw):
            continue
        for (r, c), (value, decimals) in grid.cells.items():
            if abs(value) * 10 ** decimals < 100 or c >= len(frame.columns):
                continue
            new = frame.iat[r, c]
            new = parse_number(new) if isinstance(new, str) else new
            if new is None or new != new:
                continue
            found.setdefault(value, []).append((table_id, c, r, round(float(new), 6)))
    out = {}
    for value, cells in found.items():
        if len({n for *_, n in cells}) == 1:
            out[value] = cells[0][3]
        elif len({(t, c) for t, c, _, _ in cells}) == 1:
            out[value] = max(cells, key=lambda cell: cell[2])[3]
    return out


def band_edges(pdf_path, page_number: int, width: float, height: float) -> tuple[float, float]:
    """(bottom of the top band or 0, top of the bottom band or the page height) of a source page."""
    from src.rendering._pages import page_bands
    try:
        top, bottom = page_bands(pdf_path, page_number, width, height)
    except Exception:
        return 0.0, height
    return (top if top <= 0.4 * height else 0.0), (bottom if bottom >= 0.8 * height else height)


_PEOPLE: set[str] | None = None


def person_words() -> set[str]:
    """Lower-case first and last names (Faker's person data for a few English locales)."""
    global _PEOPLE
    if _PEOPLE is None:
        words: set[str] = set()
        try:
            import importlib
            for locale in ("en_IN", "en_US", "en_GB"):
                provider = importlib.import_module(f"faker.providers.person.{locale}").Provider
                for attr in ("first_names", "first_names_male", "first_names_female", "last_names"):
                    values = getattr(provider, attr, ()) or ()
                    words |= {str(v).lower() for v in (values.keys() if isinstance(values, dict) else values)}
        except Exception:
            pass
        _PEOPLE = {w for w in words if len(w) >= 3}
    return _PEOPLE


PERSON_RE = re.compile(r"\b([A-Z][a-z]+)(\s+[A-Z]\.?)?(\s+[A-Z][a-z]+)\b")


def fake_people(text: str, rng, keep: set[str] = frozenset(), vocabulary: set[str] = frozenset()) -> str:
    """A person's name ('Manan Goyal', 'Vinod T P') is swapped for a generated one of the same shape;
    words of the organisation or publisher (`keep`) are never treated as a name."""
    people = person_words()
    from src.core.originality import GENERIC_WORDS, common_words

    def ordinary(word: str) -> bool:           # 'Target Price': both are words of the field, not a name
        w = word.lower()
        return w in GENERIC_WORDS or w in common_words() or w in vocabulary

    def swap(m):
        words = [m.group(1), m.group(3).strip()]
        if any(w.lower() in keep for w in words) or not all(w.lower() in people for w in words) \
                or any(ordinary(w) for w in words):
            return m.group(0)
        try:
            from faker import Faker
            fake = Faker("en_IN")
            fake.seed_instance(int(rng.integers(1 << 30)))
            first, last = fake.first_name(), fake.last_name()
        except Exception:
            return m.group(0)
        middle = m.group(2) or ""
        return f"{first}{middle}{(' ' + last) if m.group(3).startswith(' ') else last}"
    return PERSON_RE.sub(swap, text)


def _shift_dates(text: str, years: int) -> str:
    def shift(m):
        year = m.group(4)
        new = (int(year) + years) % 100 if len(year) == 2 else int(year) + years
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(2)}{new:02d}" if len(year) == 2 else \
            f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(2)}{new}"
    return DATE_RE.sub(shift, text)


def masthead_text(text: str, mapping: dict[float, float], rng: np.random.Generator, years: int,
                  distance: float = 0.05) -> str:
    text = ORDINAL_RE.sub(r"\1\2", text or "")
    protected: list[tuple[int, int]] = [m.span() for m in CODE_RE.finditer(text)]
    protected += [m.span() for m in DATE_RE.finditer(text)]
    found = []
    for m in NUMBER_RE.finditer(text):
        if any(a <= m.start(3) < b for a, b in protected):
            continue
        token = m.group(3)
        value = parse_number(token)
        if value is None:
            continue
        currency, percent = bool(m.group(2)), bool(m.group(4))
        if not currency and not percent and re.fullmatch(r"(19|20)\d\d", token):
            continue                                  # a year: shifted with the periods below
        if not (currency or percent or "," in token or "." in token or abs(value) >= 100):
            continue                                  # a count in the wording ('12 Months')
        found.append((m, value, percent))

    def jitter(value: float, low: float = 0.78, high: float = 1.25) -> float:
        return value * float(rng.choice([rng.uniform(low, 1 - distance * 1.2), rng.uniform(1 + distance * 1.2, high)]))

    new_values: dict[int, float] = {}
    for i, (m, value, percent) in enumerate(found):
        if not percent and value in mapping:
            new_values[i] = mapping[value]
    # a percentage that is the change between two figures of the box (RETURN = TARGET / CMP - 1):
    # with one figure fixed by a table, the % moves and the other figure follows from it
    links: dict[int, tuple[int, int]] = {}
    for i, (m, value, percent) in enumerate(found):
        if not percent:
            continue
        figures = [(j, v) for j, (_, v, p) in enumerate(found) if not p]
        for (ja, a), (jb, b) in ((x, y) for x in figures for y in figures if x[0] != y[0]):
            if b and abs((a / b - 1) * 100 - value) <= 1.0:
                links[i] = (ja, jb)
                break
    for i, (ja, jb) in links.items():
        pct = jitter(found[i][1], 0.7, 1.4)
        if ja in new_values and jb not in new_values:
            new_values[jb] = new_values[ja] / (1 + pct / 100)
        elif jb in new_values and ja not in new_values:
            new_values[ja] = new_values[jb] * (1 + pct / 100)
    for i, (m, value, percent) in enumerate(found):
        if not percent and i not in new_values:
            new_values[i] = jitter(value)
    for i, (m, value, percent) in enumerate(found):
        if not percent:
            continue
        if i in links:
            ja, jb = links[i]
            new_values[i] = (new_values[ja] / new_values[jb] - 1) * 100
        else:
            new_values[i] = jitter(value, 0.75, 1.3)
    out, last = [], 0
    for i, (m, value, percent) in enumerate(found):
        out.append(text[last:m.start(3)])
        new = new_values[i]
        if value and abs(new - value) / abs(value) < distance:
            new = value * (1 + distance * 1.5)
        out.append(format_number_like(m.group(3), new))
        last = m.end(3)
    out.append(text[last:])
    return _shift_dates(shift_text_periods("".join(out), years), years)


# ---------------------------------------------------------------- span layer (exact masthead look)
SPANS_MARK = "\u0002spans:"            # a block text carrying absolutely placed source spans
_JOIN = "\u0001"


def source_spans(pdf_path, page_number: int, bbox) -> list[dict]:
    """The source text spans inside a block box, in reading order: position, font, size, colour. A
    span that uses a run of spaces as a tab stop ('KEY CHANGES:        TARGET') is split there, each
    piece at its own x, so a narrower font cannot pull the pieces together."""
    import fitz
    out = []
    with fitz.open(pdf_path) as document:
        if not 1 <= page_number <= document.page_count:
            return out
        box = fitz.Rect(bbox) + (-1, -1, 1, 1)
        for block in document[page_number - 1].get_text("rawdict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    chars = span.get("chars") or []
                    rect = fitz.Rect(span["bbox"])
                    if not chars or not box.contains(fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)):
                        continue
                    pieces, current = [], []
                    for i, ch in enumerate(chars):
                        if ch["c"] == " " and i + 1 < len(chars) and chars[i + 1]["c"] == " ":
                            if current:
                                pieces.append(current)
                            current = []
                            continue
                        if ch["c"] == " " and not current:
                            continue
                        current.append(ch)
                    if current:
                        pieces.append(current)
                    for piece in pieces:
                        text = "".join(ch["c"] for ch in piece).strip()
                        if not text:
                            continue
                        x0 = min(ch["bbox"][0] for ch in piece)
                        x1 = max(ch["bbox"][2] for ch in piece)
                        out.append({"text": text, "bbox": [x0, rect.y0, x1, rect.y1], "font": span["font"],
                                    "size": round(span["size"], 2), "color": f"#{span['color']:06X}"})
    return out


def transform_spans(spans: list[dict], transform) -> list[dict]:
    """Apply a whole-box text transform (numbers related across spans) and split it back per span."""
    joined = _JOIN.join(s["text"] for s in spans)
    new = transform(joined)
    parts = new.split(_JOIN)
    if len(parts) != len(spans):                     # the transform merged spans: keep the source split
        parts = [transform(s["text"]) for s in spans]
    return [{**s, "text": t} for s, t in zip(spans, parts)]


def spans_text(spans: list[dict]) -> str:
    import json
    return SPANS_MARK + json.dumps(spans, ensure_ascii=False)
