# src/rendering/qa.py
"""QA (M8.12): deterministic gates first, then the visual critic when a vision model is available.

`qa()` returns a `QAReport`; `pdf_path` is the kept round's PDF (the deterministic pass when the
critic does not run)."""
from __future__ import annotations

from pathlib import Path

from src.contracts.report import QAReport


def qa(pdf_path, templates, style, tables=None, sidecars=None, avoid=None, source_pdf=None,
       cfg=None, vision=None, render_round=None, source_pages=None,
       unchartable=None) -> QAReport:
    from src.rendering._critic import param_specs, run_critic
    from src.rendering._qa_pages import check_pages

    min_words = cfg.report.min_total_words if cfg is not None else 1000
    tolerance = cfg.report.word_tolerance if cfg is not None else 0.15
    report = check_pages(pdf_path, templates, style, tables, sidecars, avoid, source_pdf,
                         min_words, tolerance, unchartable)
    final = Path(pdf_path)
    round_scores: list[list[int]] = []
    kept_round, manual = 0, []
    if vision is not None and getattr(vision, "enabled", False) and render_round and source_pages:
        result = run_critic(render_round, source_pages, param_specs(cfg), cfg, vision)
        final = Path(result["pdf"])
        report = check_pages(final, templates, style, tables, sidecars, avoid, source_pdf,
                             min_words, tolerance, unchartable)
        round_scores = result["round_scores"]
        kept_round = result["kept_round"]
        manual = result["manual_review"]
    return QAReport(gates=report["gates"], failures=report["failures"],
                    round_scores=round_scores, kept_round=kept_round,
                    manual_review_required=manual, pdf_path=str(final))
