# src/extraction/_pymupdf.py
"""Fallback extraction with PyMuPDF only, used when docling is missing or fails. Same folder layout
as the docling path, so the blueprint code reads either one unchanged."""
from __future__ import annotations

import csv
import json
from pathlib import Path

MIN_FIGURE_PT = 60          # smaller images and drawing clusters are icons or rules, not figures
FIGURE_ZOOM = 2.0


def _clean(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _figure_rects(page) -> list:
    """Embedded raster images plus clusters of vector drawings (charts drawn as paths)."""
    import pymupdf
    rects = []
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"])
        if r.width >= MIN_FIGURE_PT and r.height >= MIN_FIGURE_PT:
            rects.append(r)
    try:
        clusters = page.cluster_drawings()
    except Exception:                              # older PyMuPDF: raster images only
        clusters = []
    for r in clusters:
        if r.width >= MIN_FIGURE_PT and r.height >= MIN_FIGURE_PT and not any(r.intersects(x) for x in rects):
            rects.append(r)
    return sorted(rects, key=lambda r: (r.y0, r.x0))


def extract(pdf_path: Path, out: Path) -> dict:
    import pymupdf
    tables_dir, figures_dir, rebuilt_dir = out / "tables", out / "figures", out / "reconstructed_tables"
    for d in (tables_dir, figures_dir, rebuilt_dir):
        d.mkdir(parents=True, exist_ok=True)

    table_summary, manifest, figure_summary = [], [], []
    markdown = []
    with pymupdf.open(str(pdf_path)) as document:
        number = 0
        for page in document:
            markdown.append(page.get_text("text"))
            table_rects = []
            for table in page.find_tables().tables:
                # cell text read from each cell's box: table.extract() can drop spaces between words
                grid = [[_clean(page.get_text("text", clip=cell)) if cell else "" for cell in row.cells]
                        for row in table.rows]
                if len(grid) < 2 or max(len(r) for r in grid) < 2:
                    continue
                number += 1
                table_rects.append(pymupdf.Rect(table.bbox))
                width = max(len(r) for r in grid)
                grid = [r + [""] * (width - len(r)) for r in grid]
                for folder in (tables_dir, rebuilt_dir):
                    with (folder / f"table_{number:03}.csv").open("w", newline="", encoding="utf-8-sig") as h:
                        csv.writer(h).writerows(grid)
                table_summary.append({"table_number": number, "rows": len(grid) - 1, "columns": width,
                                      "csv_path": f"table_{number:03}.csv"})
                manifest.append({"table_number": number, "page": page.number + 1, "rows": len(grid),
                                 "columns": width, "merged_cell_count": 0, "overlap_count": 0,
                                 "merged_cells": [], "overlaps": [], "csv_path": f"table_{number:03}.csv"})
            for rect in _figure_rects(page):
                if any(rect.intersects(t) for t in table_rects):
                    continue                        # table ruling lines, not a figure
                index = len(figure_summary) + 1
                path = figures_dir / f"figure_{index:03}.png"
                page.get_pixmap(matrix=pymupdf.Matrix(FIGURE_ZOOM, FIGURE_ZOOM), clip=rect).save(str(path))
                figure_summary.append({"figure_number": index, "page": page.number + 1, "caption": "",
                                       "image_path": path.name})
    (rebuilt_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (out / "document.md").write_text("\n\n".join(markdown), encoding="utf-8")
    return {"source_pdf": pdf_path.name, "conversion_status": "pymupdf", "table_count": len(table_summary),
            "tables": table_summary, "detected_figure_count": len(figure_summary),
            "saved_figures": figure_summary}
