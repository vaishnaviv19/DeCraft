# src/style/__init__.py  -- PUBLIC INTERFACE of the style module (deep module, M8.1-M8.3)
"""Everything learned about the SOURCE's look: fonts, Text Styles, palette, page geometry, Page
Templates with positioned Blocks, and the Figure Analyst's labels.

The interface is fixed here first (v3 section 2.3). `extract_style` is implemented from the PDF with
PyMuPDF; `build_templates` and `analyse_figures` follow in M8.2 and M8.3.
"""
from __future__ import annotations

from pathlib import Path

from src.config import Config
from src.contracts import SourceDocument
from src.contracts.report import FigureInfo, PageTemplate, RecoveredChart, StyleProfile
from src.llm import LLMGateway

__all__ = ["extract_style", "build_templates", "analyse_figures"]


def extract_style(pdf_path: Path, cfg: Config) -> StyleProfile:
    """Fonts, Text Styles, palette, page size, margins, borders, running header/footer."""
    raise NotImplementedError("M8.1: style.extract_style")


def build_templates(pdf_path: Path, doc: SourceDocument, style: StyleProfile,
                    cfg: Config) -> list[PageTemplate]:
    """One Page Template per source page, Block classification and mirror/flow mode."""
    raise NotImplementedError("M8.2: style.build_templates")


def analyse_figures(pdf_path: Path, templates: list[PageTemplate], cfg: Config,
                    gateway: LLMGateway | None) -> tuple[list[FigureInfo], list[RecoveredChart]]:
    """Figure Analyst labels, and data recovered from charts that have no table."""
    raise NotImplementedError("M8.3: style.analyse_figures")
