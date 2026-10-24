# src/blueprint/validate_pdf.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from pypdf import PdfReader
from pypdf.errors import PdfReadError, DependencyError


class PDFValidationError(Exception):
    """Clear, actionable errors for PDF validation."""
    pass


def validate_pdf(pdf_path: str | Path) -> Dict[str, Any]:
    """
    FR-A1: Accept a PDF path and validate it
    - exists
    - is a valid PDF
    - is not encrypted
    - has at least 1 page
    Fail with a clear error otherwise.
    """
    path = Path(pdf_path)

    if not path.exists():
        raise PDFValidationError(f"PDF not found: {path}")

    if not path.is_file():
        raise PDFValidationError(f"Path is not a file: {path}")

    if path.suffix.lower() != ".pdf":
        raise PDFValidationError(f"File does not have .pdf extension: {path.name}")

    if not os.access(path, os.R_OK):
        raise PDFValidationError(f"File is not readable (permission denied): {path}")

    try:
        reader = PdfReader(str(path))
    except PdfReadError as e:
        raise PDFValidationError(f"Invalid PDF structure: {e}") from e
    except DependencyError as e:
        raise PDFValidationError(f"Missing PDF dependency: {e}") from e
    except Exception as e:
        raise PDFValidationError(f"Failed to open PDF: {e}") from e

    if reader.is_encrypted:
        try:
            if reader.decrypt("") == 0:
                raise PDFValidationError("PDF is encrypted and cannot be opened without a password.")
        except PDFValidationError:
            raise
        except Exception:
            raise PDFValidationError("PDF is encrypted. Provide an unencrypted PDF.")

    try:
        num_pages = len(reader.pages)
    except Exception as e:
        raise PDFValidationError(f"Cannot read page count: {e}") from e

    if num_pages < 1:
        raise PDFValidationError("PDF has no pages.")

    meta = reader.metadata or {}
    title = None
    author = None
    subject = None
    try:
        title = getattr(meta, "title", None)
        if not title and isinstance(meta, dict):
            title = meta.get("/Title")
        author = getattr(meta, "author", None)
        if not author and isinstance(meta, dict):
            author = meta.get("/Author")
        subject = getattr(meta, "subject", None)
        if not subject and isinstance(meta, dict):
            subject = meta.get("/Subject")
    except Exception:
        pass

    info = {
        "path": str(path.resolve()),
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "num_pages": num_pages,
        "is_encrypted": bool(reader.is_encrypted),
        "title": title,
        "author": author,
        "subject": subject,
    }
    return info


if __name__ == "__main__":
    import sys
    info = validate_pdf(sys.argv[1])
    print(info)
