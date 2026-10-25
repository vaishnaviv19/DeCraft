# src/blueprint/to_source_document.py
"""Adapter from the frozen Blueprint to the SourceDocument used by every new module (M1.1).

This is the only change M1 makes to `src/blueprint/`. It reads the Source CSVs and keeps every Raw
Cell string exactly as extracted; the Blueprint itself and its output file are not modified.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from src.contracts import SourceDocument, SourceTable

EXTRACTION_METHODS = {"reconstructed_tables": "docling_reconstructed_csv", "tables": "docling_csv"}



def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _table_pages(csv_path: Path) -> dict[str, int]:
    """Read the reconstructed-table manifest next to the CSVs; it is the only record of table pages."""
    manifest = csv_path.parent / "manifest.json"
    if not manifest.exists():
        return {}
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    pages = {}
    for e in entries if isinstance(entries, list) else []:
        number = e.get("table_number")
        if isinstance(number, int) and isinstance(e.get("page"), int):
            pages[f"table_{number:03d}"] = e["page"]
    return pages


def _read_raw_cells(csv_path: Path) -> tuple[list[str], list[list[str]]]:
    """Raw Cells as strings; empty cells stay empty strings, never NaN."""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, encoding=encoding)
        except UnicodeDecodeError:
            continue
        except (pd.errors.ParserError, OSError):
            return [], []
        header = [str(c) for c in df.columns]
        return header, [[str(v) for v in row] for row in df.itertuples(index=False, name=None)]
    return [], []


def to_source_document(blueprint: Mapping[str, Any], document_sha256: str | None = None) -> SourceDocument:
    """Build the immutable SourceDocument view of one Blueprint.

    `document_sha256` may be supplied by a caller that already hashed the Source PDF; otherwise the
    file named in the Blueprint is hashed. A missing Source CSV yields an empty Source Table, which
    `grade_table` marks `bad` so the Run logs it instead of guessing.
    """
    source = blueprint.get("source") or {}
    pdf_path = source.get("pdf_path")
    sha = document_sha256 or (_sha256(Path(pdf_path)) if pdf_path and Path(pdf_path).is_file() else "")

    blocks = [str(b.get("text", "")) for b in blueprint.get("text_blocks") or [] if str(b.get("text", "")).strip()]
    page_text: dict[int, str] = {}
    for b in blueprint.get("text_blocks") or []:
        page = b.get("page")
        if isinstance(page, int):
            page_text.setdefault(page, []).append(str(b.get("text", "")))
    page_text = {p: " ".join(t).strip() for p, t in page_text.items()}

    pages = {}
    for table in blueprint.get("tables") or []:
        csv = table.get("source_csv")
        if csv:
            pages.update(_table_pages(Path(csv)))

    tables: list[SourceTable] = []
    for table in blueprint.get("tables") or []:
        table_id = str(table.get("table_id", ""))
        csv = table.get("source_csv")
        header, raw_cells = ([str(h) for h in table.get("column_headers") or []], [])
        if csv:
            header_from_csv, cells = _read_raw_cells(Path(csv))
            if header_from_csv:
                header, raw_cells = header_from_csv, cells
        page = pages.get(table_id, 1)
        tables.append(SourceTable(
            table_id=table_id,
            page=page,
            caption=table.get("caption"),
            header=header,
            raw_cells=raw_cells,
            extraction_method=EXTRACTION_METHODS.get(Path(csv).parent.name, "csv") if csv else "unavailable",
            ocr=bool(table.get("ocr", False)),
            context_text=page_text.get(page, ""),
        ))
    tables.sort(key=lambda t: t.table_id)

    entity = blueprint.get("entity") or {}
    validation = blueprint.get("validation") or {}
    title = validation.get("title") or (Path(source["filename"]).stem if source.get("filename") else None)
    return SourceDocument(
        document_sha256=sha,
        title=title,
        entity_name=entity.get("entity_name"),
        tables=tables,
        text_blocks=blocks,
    )
