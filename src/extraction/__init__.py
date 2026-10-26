# src/extraction/__init__.py  -- PUBLIC INTERFACE of the extraction module (deep module)
"""Parse the run's own input PDF into tables, figures and structure (E-37, task 4).

Writes `<root>/outputs/structured/` in the layout the frozen blueprint code reads, so
`build_blueprint(pdf, project_root=root)` works unchanged on any PDF. Docling is used when installed
(the notebook method); otherwise, or when it fails, PyMuPDF. Results are cached by the PDF's sha256 -
a cache entry is only ever reused for the identical file.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Literal

__all__ = ["extract_structured", "structured_dir", "sha256_file"]

Method = Literal["auto", "docling", "pymupdf"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def structured_dir(root: Path) -> Path:
    return Path(root) / "outputs" / "structured"


def _run(pdf: Path, out: Path, method: Method, models_dir: str | None) -> dict:
    warnings = []
    if method in ("auto", "docling"):
        try:
            from src.extraction import _docling
            return _docling.extract(pdf, out, models_dir) | {"method": "docling", "warnings": warnings}
        except Exception as error:               # missing package, models or a parse failure
            if method == "docling":
                raise
            warnings.append(f"docling failed ({type(error).__name__}: {error}); used pymupdf")
            shutil.rmtree(out, ignore_errors=True)
            out.mkdir(parents=True)
    from src.extraction import _pymupdf
    return _pymupdf.extract(pdf, out) | {"method": "pymupdf", "warnings": warnings}


def extract_structured(pdf_path: str | Path, root: str | Path, cache_dir: str | Path | None = None,
                       method: Method = "auto", models_dir: str | None = None) -> dict:
    """Extract `pdf_path` into `structured_dir(root)` and return the extraction summary
    (with `source_sha256`, `method`, `warnings`, `cached`)."""
    pdf, root = Path(pdf_path), Path(root)
    sha = sha256_file(pdf)
    out = structured_dir(root)
    if out.exists():
        shutil.rmtree(out)
    cached = Path(cache_dir) / sha / "structured" if cache_dir else None
    summary_name = "extraction_summary.json"

    if cached is not None and (cached / summary_name).exists():
        summary = json.loads((cached / summary_name).read_text(encoding="utf-8"))
        if summary.get("source_sha256") == sha and method in ("auto", summary.get("method")):
            shutil.copytree(cached, out)
            return summary | {"cached": True}

    out.mkdir(parents=True)
    summary = _run(pdf, out, method, models_dir) | {"source_sha256": sha}
    (out / summary_name).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    if cached is not None:
        if cached.exists():
            shutil.rmtree(cached)
        shutil.copytree(out, cached)
    return summary | {"cached": False}
