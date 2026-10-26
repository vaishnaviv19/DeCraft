# src/rendering/_html.py
"""HTML rendering (M8.5): Jinja2 templates; tables and charts read from the Display Tables.

The page imitates the source document: its page size and margins, body font and size, heading sizes
and palette come from the Blueprint; every section carries its own tables and charts (Document Plan);
cells are formatted like the source cells (thousands separators, decimals, %, currency, negatives in
brackets, the source's blank token) and columns keep the source order. Nothing in the HTML states how
the report was produced.
"""
from __future__ import annotations

import html as html_lib
import re
from collections import Counter

import pandas as pd
from jinja2 import Template

from src.contracts.dataset import CanonicalDataset
from src.core.missingness import TOKEN_TEXT, token_kind
from src.rendering._charts import chart_caption

PAGE = Template("""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{{ title }}</title>
<style>
@page { size: {{ page_w }}pt {{ page_h }}pt; margin: {{ m_top }}pt {{ m_side }}pt {{ m_bottom }}pt {{ m_side }}pt; }
body { font-family: {{ font }}; font-size: {{ body_pt }}pt; color: {{ text_color }}; margin: 0; line-height: 1.35; }
h1 { font-size: {{ h1_pt }}pt; color: {{ accent }}; margin: 0 0 6pt 0; }
h2 { font-size: {{ h2_pt }}pt; color: {{ accent }}; margin: 12pt 0 4pt 0; }
h3 { font-size: {{ h3_pt }}pt; margin: 8pt 0 3pt 0; }
p { margin: 0 0 6pt 0; text-align: justify; }
table { border-collapse: collapse; width: 100%; font-size: {{ body_pt }}pt; margin: 4pt 0 8pt 0; }
thead { display: table-header-group; } tr { page-break-inside: avoid; }
h3 { page-break-after: avoid; }
th { background: {{ header_bg }}; color: {{ text_color }}; font-weight: bold; }
th, td { border-bottom: 0.5pt solid {{ rule }}; padding: 0.6pt 3pt; text-align: right; line-height: 1.15; }
th:first-child, td:first-child { text-align: left; }
figure { display: inline-block; width: {{ chart_w }}pt; margin: 2pt {{ chart_gap }}pt 6pt 0; vertical-align: top; page-break-inside: avoid; }
img { width: {{ chart_w }}pt; }   /* drawn at this exact width: chart text keeps the body size (E-38) */
figcaption { font-size: {{ body_pt }}pt; color: {{ text_color }}; }
</style></head><body>
<h1>{{ title }}</h1>
{% for block in blocks %}
{% if block.kind == "section" %}<section>{% if block.title != title %}<h2>{{ block.title }}</h2>{% endif %}{% for p in block.paragraphs %}<p>{{ p }}</p>{% endfor %}</section>
{% elif block.kind == "table" %}<h3>{{ block.title }}</h3>{{ block.html | safe }}
{% else %}<figure><img src="{{ block.src }}" alt="{{ block.title }}"><figcaption>{{ block.title }}</figcaption></figure>{% endif %}
{% endfor %}
</body></html>
""")

CHART_GAP = 6.0          # pt between charts placed side by side

GENERIC_TITLE = re.compile(r"^Page \d+ - .* Content$")
# E-25 / E-36: domain-free titles, never a source heading (used until the Titles agent writes new ones)
FALLBACK_TITLES = ["Overview", "Highlights", "Review", "Key figures", "Analysis", "Outlook"]


# ---------------------------------------------------------------- style from the blueprint
def _luminance(hex_color: str) -> float:
    try:
        r, g, b = (int(hex_color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return 1.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _grey(hex_color: str) -> bool:
    try:
        r, g, b = (int(hex_color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return True
    return max(r, g, b) - min(r, g, b) < 18


def style_from(blueprint) -> dict:
    bp = blueprint if isinstance(blueprint, dict) else {}
    blocks = bp.get("text_blocks") or []
    fonts = Counter(re.split(r"[-,]", str(b.get("font_name", "")))[0] for b in blocks if b.get("font_name"))
    sizes = Counter(round(float(b["font_size"])) for b in blocks if b.get("font_size"))
    body_pt = sizes.most_common(1)[0][0] if sizes else 10
    heads = sorted({round(float(h.get("font_size", 0))) for h in bp.get("headings") or [] if h.get("font_size")},
                   reverse=True)
    palette = bp.get("color_palette") or {}
    colors = [c for c in (palette.get("accent") or []) + (palette.get("dominant") or []) if isinstance(c, str)]
    accent = next((c for c in colors if not _grey(c) and _luminance(c) < 0.6), "#1f3b57")
    header_bg = next((c for c in colors if 0.85 < _luminance(c) < 0.99), "#eef1f4")
    page = (bp.get("page_structure") or [{}])[0]
    size, margins = page.get("size") or {}, page.get("margins") or {}
    side = max(float(margins.get("left", 36) or 36), float(margins.get("right", 36) or 36))
    family = fonts.most_common(1)[0][0] if fonts else ""
    return {
        "font": (f'"{family}", ' if family else "") + "Georgia, \"Times New Roman\", serif",
        # E-38 Font Lock: tables, captions and chart text use the source body size; headings use the
        # source heading sizes; only padding, spacing and box sizes are ours
        "body_pt": body_pt,
        "h1_pt": heads[0] if heads else body_pt + 8, "h2_pt": heads[1] if len(heads) > 1 else body_pt + 4,
        "h3_pt": heads[2] if len(heads) > 2 else body_pt + 1,
        "accent": accent, "header_bg": header_bg, "rule": "#bbbbbb",
        "text_color": next((c for c in colors if _luminance(c) < 0.45 and _grey(c)), "#1a1a1a"),
        "page_w": float(size.get("width", 595.3)), "page_h": float(size.get("height", 841.9)),
        "m_side": side, "m_top": float(margins.get("top", 36) or 36), "m_bottom": float(margins.get("bottom", 36) or 36),
        "chart_colors": [c for c in colors if not _grey(c)][:6] or None,
        "chart_w": round((float(size.get("width", 595.3)) - 2 * side - 2 * CHART_GAP) / 3, 1),
        "chart_gap": CHART_GAP,
        "chart_font": family or None,
    }


# ---------------------------------------------------------------- cells formatted like the source
def column_style(raw: list) -> dict:
    """How the SOURCE wrote this column: brackets for negatives, currency, %, the blank token."""
    text = [str(v).strip() for v in raw if v is not None and str(v).strip()]
    kinds = Counter(k for v in raw if (k := token_kind(v)) is not None and k != "null")
    currency = next((s for s in ("$", "₹", "€", "£") if any(v.startswith(s) for v in text)), "")
    return {"brackets": any(v.startswith("(") and v.endswith(")") for v in text),
            "percent": sum(v.endswith("%") for v in text) > len(text) / 2 if text else False,
            "currency": currency, "blank": TOKEN_TEXT.get(kinds.most_common(1)[0][0], "") if kinds else ""}


def format_number(value, decimals: int | None, style: dict) -> str:
    if value is None or pd.isna(value):
        return style.get("blank", "")
    places = decimals if decimals is not None else (0 if float(value).is_integer() else 2)
    body = f"{abs(float(value)):,.{places}f}"
    body = f"{style.get('currency', '')}{body}{'%' if style.get('percent') else ''}"
    if float(value) < 0:
        return f"({body})" if style.get("brackets") else f"-{body}"
    return body


def table_html(display: pd.DataFrame, profiles: list, source_table=None) -> str:
    order = [p.column for p in profiles if p.column in display.columns]
    columns = order + [c for c in display.columns if c not in order]
    decimals = {p.column: p.decimals for p in profiles}
    header = list(source_table.header) if source_table is not None else []
    styles = {}
    for c in columns:
        j = header.index(c) if c in header else None
        raw = [row[j] if j is not None and j < len(row) else None for row in (source_table.raw_cells if j is not None else [])]
        styles[c] = column_style(raw)
    out = ["<table><thead><tr>" + "".join(f"<th>{html_lib.escape(str(c))}</th>" for c in columns) + "</tr></thead><tbody>"]
    for i in range(len(display)):
        cells = []
        for c in columns:
            v = display[c].iloc[i]
            if pd.api.types.is_numeric_dtype(display[c]):
                cells.append(format_number(v, decimals.get(c), styles[c]))
            else:
                cells.append(styles[c]["blank"] if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
        out.append("<tr>" + "".join(f"<td>{html_lib.escape(x)}</td>" for x in cells) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


# ---------------------------------------------------------------- page content
def document_title(blueprint, doc=None) -> str:
    bp = blueprint if isinstance(blueprint, dict) else {}
    for candidate in ((bp.get("entity") or {}).get("entity_name"), getattr(doc, "entity_name", None),
                      getattr(doc, "title", None),
                      next((h.get("text") for h in bp.get("headings") or [] if h.get("level") in ("H1", "H2")), None)):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return "Annual report"


def section_title(section: dict, index: int, blueprint) -> str:
    """A new title from the plan, else a domain-free fallback; a source heading is never copied (E-36)."""
    title = str(section.get("title") or "").strip()
    bp = blueprint if isinstance(blueprint, dict) else {}
    source_headings = {str(h.get("text", "")).strip().lower() for h in bp.get("headings") or []}
    if title and not GENERIC_TITLE.match(title) and title.lower() not in source_headings:
        return title
    return FALLBACK_TITLES[index % len(FALLBACK_TITLES)]


def build_html(data: CanonicalDataset, narrative, blueprint=None, plan=None, doc=None) -> str:
    style = style_from(blueprint)
    sources = {t.table_id: t for t in getattr(doc, "tables", [])} if doc is not None else {}
    profiles = {}
    for p in data.source_profiles:
        profiles.setdefault(p.table_id, []).append(p)
    table_numbers = {table_id: n for n, table_id in enumerate(data.display, start=1)}

    def table_block(table_id):
        source = sources.get(table_id)
        title = f"Table {table_numbers[table_id]}"           # E-36: source captions are never reused
        return {"kind": "table", "title": title,
                "html": table_html(data.display[table_id], profiles.get(table_id, []), source)}

    def chart_block(chart_id):
        return {"kind": "chart", "src": f"charts/{chart_id}.svg", "title": chart_caption(data.charts[chart_id])}

    blocks, placed_tables, placed_charts = [], set(), set()
    plan_sections = list((plan or {}).get("sections", [])) if isinstance(plan, dict) else []
    for index, section in enumerate(narrative.sections):
        planned = next((s for s in plan_sections if str(s.get("section_id")) == section.section_id), {})
        title = section_title({**planned, "title": planned.get("title", section.title)}, index, blueprint)
        paragraphs = [p.strip() for p in section.text.split("\n\n") if p.strip()]
        blocks.append({"kind": "section", "title": title, "paragraphs": paragraphs})
        for t in planned.get("tables", []) or []:
            table_id = t.get("table_id") if isinstance(t, dict) else t
            if table_id in data.display and table_id not in placed_tables:
                blocks.append(table_block(table_id))
                placed_tables.add(table_id)
        for c in planned.get("charts", []) or []:
            chart_id = c.get("chart_id") if isinstance(c, dict) else c
            if chart_id in data.charts and chart_id not in placed_charts:
                blocks.append(chart_block(chart_id))
                placed_charts.add(chart_id)
    blocks += [table_block(t) for t in data.display if t not in placed_tables]
    blocks += [chart_block(c) for c in data.charts if c not in placed_charts]
    return PAGE.render(title=document_title(blueprint, doc), blocks=blocks,
                       **{k: v for k, v in style.items() if k not in ("chart_colors", "chart_font")})
