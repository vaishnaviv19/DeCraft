# src/style/__init__.py  -- PUBLIC INTERFACE of the style module (deep module, M8.1-M8.3)
"""Everything learned about the SOURCE's look: fonts, Text Styles, palette, page geometry, Page
Templates with positioned Blocks, and the Figure Analyst's labels.

The interface is fixed here first (v3 section 2.3). `extract_style` is implemented from the PDF with
PyMuPDF; `build_templates` and `analyse_figures` follow in M8.2 and M8.3.
"""
from __future__ import annotations

from src.style._extract import extract_style
from src.style._figures import analyse_figures
from src.style._templates import build_templates, numeric_share

__all__ = ["extract_style", "build_templates", "analyse_figures", "numeric_share"]
