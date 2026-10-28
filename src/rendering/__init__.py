# src/rendering/__init__.py  -- PUBLIC INTERFACE of the rendering module (deep module)
"""Render the report: charts (matplotlib SVG), HTML (Jinja2), PDF (WeasyPrint) and Rendered-PDF QA.

WeasyPrint needs the Pango/GTK runtime. The vendored copy at `vendor/gtk` is used when present, so the
renderer works without a system install; otherwise the Run reports the reason instead of switching
renderers silently.
"""
from __future__ import annotations

import os
from pathlib import Path

from src.config import Config
from src.contracts import RenderResult
from src.contracts.dataset import CanonicalDataset
from src.rendering._charts import draw_svg, write_sidecar
from src.rendering._html import build_html, document_title, style_from
from src.rendering._qa import run_qa

__all__ = ["render"]

VENDOR_GTK = Path(__file__).resolve().parents[2] / "vendor" / "gtk"


def _prepare_weasyprint() -> None:
    """Point WeasyPrint at the vendored GTK runtime, unless the environment already says otherwise."""
    if not VENDOR_GTK.exists():
        return
    os.environ.setdefault("WEASYPRINT_DLL_DIRECTORIES", str(VENDOR_GTK / "bin"))
    fonts = VENDOR_GTK / "etc" / "fonts"
    if fonts.exists():
        os.environ.setdefault("FONTCONFIG_PATH", str(fonts))
    os.environ["PATH"] = str(VENDOR_GTK / "bin") + os.pathsep + os.environ.get("PATH", "")


def _source_metadata(blueprint) -> dict:
    """The source PDF's own creator / producer, so the file looks like its source. The source 'author' is
    often a real person's name and is never copied (task 6); the organisation is the author."""
    path = ((blueprint or {}).get("source") or {}).get("pdf_path") if isinstance(blueprint, dict) else None
    if not path or not Path(path).exists():
        return {}
    try:
        import pymupdf
        with pymupdf.open(path) as source:
            meta = source.metadata or {}
        return {k: meta.get(k, "") for k in ("title", "creator", "producer")}
    except Exception:                                   # unreadable source metadata: leave it out
        return {}


def write_pdf_metadata(pdf_path: Path, title: str, blueprint, author: str | None = None) -> None:
    """Document Info like an ordinary published document (E-32): the report title, the organisation as
    author, and the source's creator/producer. No synthetic marker - that lives only in the manifest."""
    import pymupdf
    with pymupdf.open(str(pdf_path)) as document:
        meta = {"title": title, "author": author or ""}
        meta.update({k: v for k, v in _source_metadata(blueprint).items() if v and k != "title"})
        from src.rendering._pages import set_clean_metadata
        set_clean_metadata(document, meta)                  # exactly these fields, no renderer name
        temp = Path(pdf_path).with_suffix(".tmp.pdf")
        document.save(str(temp))
    temp.replace(pdf_path)


def _has_numbers(data: CanonicalDataset, table_id) -> bool:
    import pandas as pd
    display = data.display.get(table_id)
    keys = set(data.key_columns.get(table_id, []))
    return display is not None and any(pd.api.types.is_numeric_dtype(display[c]) and c not in keys
                                       and display[c].notna().any() for c in display.columns)


def render(data: CanonicalDataset, narrative, blueprint, cfg: Config, out_dir: Path,
           plan: dict | None = None, doc=None) -> RenderResult:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    style = style_from(blueprint)
    chart_paths: dict[str, str] = {}
    for chart_id in data.charts:
        svg = draw_svg(data, chart_id, out_dir / "charts" / f"{chart_id}.svg",
                       style["chart_colors"], style["chart_font"], style["body_pt"], style["chart_w"])
        write_sidecar(data, chart_id, out_dir / "charts" / f"{chart_id}.json")
        chart_paths[chart_id] = str(svg)

    html = build_html(data, narrative, blueprint, plan, doc)
    html_path = out_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")

    qa: dict = {"html": "ok", "charts": len(chart_paths)}
    pdf_path: Path | None = None
    _prepare_weasyprint()
    try:
        from weasyprint import HTML
        pdf_path = out_dir / "output.pdf"
        HTML(string=html, base_url=str(out_dir)).write_pdf(str(pdf_path))
        write_pdf_metadata(pdf_path, document_title(blueprint, doc), blueprint,
                           ((blueprint or {}).get("entity") or {}).get("entity_name") if isinstance(blueprint, dict)
                           else getattr(doc, "entity_name", None))
        qa["pdf"] = "rendered"
    except Exception as error:                       # ImportError or a missing native library
        qa["pdf"] = f"not rendered: {type(error).__name__}: {error}"

    pages = 0
    if pdf_path is not None:
        planned = None
        if isinstance(blueprint, dict):
            planned = len(blueprint.get("page_structure") or []) or None
        planned_charts, skipped = None, []
        if isinstance(plan, dict):
            wanted = [c for sec in plan.get("sections", []) for c in sec.get("charts") or []]
            chartable = [c for c in wanted if _has_numbers(data, c.get("data_source_table_id"))]
            skipped = [f"{c.get('chart_id')}: table {c.get('data_source_table_id')} has no number column"
                       for c in wanted if c not in chartable]
            planned_charts = len(chartable)
        result = run_qa(pdf_path, data, out_dir, planned_pages=planned, planned_charts=planned_charts)
        result.notes.extend(skipped)
        qa["rendered_pdf_qa"] = result.as_dict()
        pages = result.pages
    return RenderResult(html_path=str(html_path), chart_paths=chart_paths,
                        pdf_path=str(pdf_path) if pdf_path else None, pages=pages, qa=qa)
