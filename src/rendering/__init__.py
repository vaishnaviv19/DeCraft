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
from src.rendering._html import build_html
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


def render(data: CanonicalDataset, narrative, blueprint, cfg: Config, out_dir: Path) -> RenderResult:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chart_paths: dict[str, str] = {}
    for chart_id in data.charts:
        svg = draw_svg(data, chart_id, out_dir / "charts" / f"{chart_id}.svg")
        write_sidecar(data, chart_id, out_dir / "charts" / f"{chart_id}.json")
        chart_paths[chart_id] = str(svg)

    html = build_html(data, narrative, blueprint)
    html_path = out_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")

    qa: dict = {"html": "ok", "charts": len(chart_paths)}
    pdf_path: Path | None = None
    _prepare_weasyprint()
    try:
        from weasyprint import HTML
        pdf_path = out_dir / "output.pdf"
        HTML(string=html, base_url=str(out_dir)).write_pdf(str(pdf_path))
        qa["pdf"] = "rendered"
    except Exception as error:                       # ImportError or a missing native library
        qa["pdf"] = f"not rendered: {type(error).__name__}: {error}"

    pages = 0
    if pdf_path is not None:
        planned = None
        if isinstance(blueprint, dict):
            planned = len(blueprint.get("page_structure") or []) or None
        result = run_qa(pdf_path, data, out_dir, planned_pages=planned)
        qa["rendered_pdf_qa"] = result.as_dict()
        pages = result.pages
    return RenderResult(html_path=str(html_path), chart_paths=chart_paths,
                        pdf_path=str(pdf_path) if pdf_path else None, pages=pages, qa=qa)
