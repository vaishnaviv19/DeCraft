# src/extraction/_docling.py
"""Docling extraction: the steps of src/extract_structured.ipynb and src/repair_tables.ipynb as code,
so every run parses the PDF it is given (E-37)."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path


def _converter(models_dir: str | None):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    models = models_dir or os.environ.get("DOCLING_ARTIFACTS_PATH")
    options = PdfPipelineOptions(
        artifacts_path=Path(models) if models else None,
        do_ocr=False,                     # native text; scanned PDFs are out of scope (they fall back)
        do_table_structure=True,
        generate_picture_images=True,
        generate_page_images=False,
    )
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


def reconstruct_table(table: dict, table_number: int) -> dict | None:
    """Merged-cell-aware grid of one docling table (text written once, at the cell's start)."""
    cells = table.get("data", {}).get("table_cells", [])
    if not cells:
        return None
    max_row = max(c["start_row_offset_idx"] + c.get("row_span", 1) for c in cells)
    max_col = max(c["start_col_offset_idx"] + c.get("col_span", 1) for c in cells)
    grid = [["" for _ in range(max_col)] for _ in range(max_row)]
    occupied, overlaps = {}, []
    for cell in cells:
        row, col = cell["start_row_offset_idx"], cell["start_col_offset_idx"]
        text = cell.get("text", "").strip()
        for r in range(row, row + cell.get("row_span", 1)):
            for c in range(col, col + cell.get("col_span", 1)):
                if (r, c) in occupied:
                    overlaps.append({"position": [r, c], "existing_text": occupied[(r, c)],
                                     "conflicting_text": text})
                else:
                    occupied[(r, c)] = text
        grid[row][col] = text
    prov = table.get("prov", [])
    merged = [{"row": c["start_row_offset_idx"], "column": c["start_col_offset_idx"],
               "row_span": c.get("row_span", 1), "column_span": c.get("col_span", 1), "text": c.get("text", "")}
              for c in cells if c.get("row_span", 1) > 1 or c.get("col_span", 1) > 1]
    return {"table_number": table_number, "page": prov[0].get("page_no") if prov else None,
            "rows": max_row, "columns": max_col, "grid": grid, "merged_cells": merged, "overlaps": overlaps}


def extract(pdf_path: Path, out: Path, models_dir: str | None = None) -> dict:
    """Write document_structure.json, document.md, tables/, reconstructed_tables/ (+ manifest),
    figures/ and extraction_summary.json under `out`. Returns the summary."""
    tables_dir, figures_dir, rebuilt_dir = out / "tables", out / "figures", out / "reconstructed_tables"
    for d in (tables_dir, figures_dir, rebuilt_dir):
        d.mkdir(parents=True, exist_ok=True)

    result = _converter(models_dir).convert(str(pdf_path))
    document = result.document
    structure = document.export_to_dict()
    (out / "document_structure.json").write_text(json.dumps(structure, indent=2, ensure_ascii=False),
                                                 encoding="utf-8")
    (out / "document.md").write_text(document.export_to_markdown(), encoding="utf-8")

    table_summary = []
    for index, table in enumerate(document.tables, start=1):
        frame = table.export_to_dataframe(doc=document)
        path = tables_dir / f"table_{index:03}.csv"
        frame.to_csv(path, index=False)
        table_summary.append({"table_number": index, "rows": int(frame.shape[0]),
                              "columns": int(frame.shape[1]), "csv_path": path.name})

    figure_summary = []
    for index, picture in enumerate(document.pictures, start=1):
        image = picture.get_image(document)
        if image is None:
            continue
        path = figures_dir / f"figure_{index:03}.png"
        image.save(path)
        page = picture.prov[0].page_no if picture.prov else None
        figure_summary.append({"figure_number": index, "page": page,
                               "caption": picture.caption_text(document), "image_path": path.name})

    manifest = []
    for number, table in enumerate(structure.get("tables", []), start=1):
        rebuilt = reconstruct_table(table, number)
        if rebuilt is None:
            continue
        path = rebuilt_dir / f"table_{number:03}.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            csv.writer(handle).writerows(rebuilt["grid"])
        manifest.append({k: rebuilt[k] for k in ("table_number", "page", "rows", "columns")}
                        | {"merged_cell_count": len(rebuilt["merged_cells"]),
                           "overlap_count": len(rebuilt["overlaps"]),
                           "merged_cells": rebuilt["merged_cells"], "overlaps": rebuilt["overlaps"],
                           "csv_path": path.name})
    (rebuilt_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"source_pdf": pdf_path.name, "conversion_status": str(result.status),
            "table_count": len(document.tables), "tables": table_summary,
            "detected_figure_count": len(document.pictures), "saved_figures": figure_summary}
