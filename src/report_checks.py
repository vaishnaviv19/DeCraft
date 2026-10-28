# src/report_checks.py  -- v3.3 output checks that combine several modules (top-level composition)
"""F-05 layout-model agreement (templates stage), and after rendering:
F-06 chart style similarity, F-08 round trip, F-09 layout metrics and the discriminator.

E-49: the round trip and the IoU compare the re-read output with the PLACED templates (source layout plus
the chart blocks M8.5.2 adds for tables that had no chart), because those chart blocks are a deliberate,
required change (task 7); comparing with the raw source would fail every run on them."""
from __future__ import annotations

import json
from pathlib import Path

CONTENT = ("heading", "text", "table", "chart", "photo", "logo", "caption")


def layout_agreement(pdf, templates, cfg, artifacts_root: Path) -> dict:
    """F-05: DocLayout-YOLO second opinion per page (flags only, E-48)."""
    from src.style._layout_check import check_templates
    if cfg.style.layout_model == "none":
        return {"available": False, "reason": "disabled in config", "agreement": {}, "flagged": []}
    weights = Path(cfg.style.layout_model_weights)
    if not weights.is_absolute():
        weights = Path(__file__).resolve().parents[1] / weights
    try:
        return check_templates(pdf, templates, weights, cfg.style.layout_min_agreement)
    except Exception as error:                               # a model failure never stops the run
        return {"available": False, "reason": f"{type(error).__name__}: {error}", "agreement": {}, "flagged": []}


def chart_style_checks(source_pdf, output_pdf, placed_templates, min_similarity: float) -> dict:
    """F-06: for every SOURCE chart (a chart block the source drew; not one M8.5.2 added), the style read
    from the source page and from the output page at the same box with the same extractor."""
    import pymupdf

    from src.style._chart_geometry import chart_style, plot_area, primitives_from_page, style_similarity
    scores, failures = {}, []
    with pymupdf.open(str(source_pdf)) as source, pymupdf.open(str(output_pdf)) as output:
        for template in placed_templates:
            for block in template.blocks:
                if block.kind != "chart" or block.block_id.endswith("c"):     # 'p2b05c' = added chart
                    continue
                if template.page > min(source.page_count, output.page_count):
                    continue
                src = primitives_from_page(source[template.page - 1], block.bbox)
                out = primitives_from_page(output[template.page - 1], block.bbox)
                value = style_similarity(chart_style(src, plot_area(src, block.bbox)),
                                         chart_style(out, plot_area(out, block.bbox)))
                scores[block.block_id] = round(value, 2)
                if value < min_similarity:
                    failures.append(f"{block.block_id}: chart style similarity {value:.2f} < {min_similarity}")
    return {"scores": scores, "failures": failures}


def structure_checks(output_pdf, placed_templates, style, cfg, source_pdf=None) -> dict:
    """F-08 round trip + F-09 metrics: our own extractor re-reads the output PDF.

    E-56 (replaces E-49's reference): the reference is the SAME extractor run on the SOURCE PDF, page by
    page. The placed templates were built with docling's tables (cell grids, attached tables) that the
    plain re-read never has, so a perfect copy of the source failed against them (p3: '0% of the text
    in its boxes'). Added chart pages (E-53) have no source page and are not part of this comparison."""
    from src.rendering._structure import roundtrip_and_metrics
    from src.style import build_templates
    reread = build_templates(Path(output_pdf), None, style, cfg)
    if source_pdf is not None and Path(source_pdf).exists():
        reference = build_templates(Path(source_pdf), None, style, cfg)
        pages = {t.page for t in reference}
        reread = [t for t in reread if t.page in pages]
        mirror = {t.page for t in placed_templates if t.mode == "mirror" and t.page in pages}
    else:
        reference = [t for t in placed_templates if not t.source_page]
        mirror = {t.page for t in reference if t.mode == "mirror"}
        reread = [t for t in reread if t.page in {t.page for t in reference}]
    return roundtrip_and_metrics(reference, reread, mirror, cfg.qa.roundtrip_min_order_sim,
                                 cfg.qa.min_block_iou, cfg.qa.max_alignment_worse_pt, cfg.qa.max_overlap_worse)


def discriminator(source_pages: list[Path], output_pages: list[Path], vision) -> dict:
    """F-09 two-alternative forced choice, asked twice per page with the order swapped."""
    from src.contracts.report import DiscriminatorOut
    from src.rendering._critic import _jpeg, _pair
    from src.rendering._prompts import DISCRIMINATOR
    from src.rendering._structure import discriminator_verdict
    verdicts, reasons = {}, {}
    for index, (src, out) in enumerate(zip(source_pages, output_pages), start=1):
        ab = vision.ask_json(DISCRIMINATOR, json.dumps({"page": index, "order": "AB"}), DiscriminatorOut,
                             images=[_pair(_jpeg(src), _jpeg(out))])
        ba = vision.ask_json(DISCRIMINATOR, json.dumps({"page": index, "order": "BA"}), DiscriminatorOut,
                             images=[_pair(_jpeg(out), _jpeg(src))])
        if ab is None or ba is None:
            continue
        verdicts[f"p{index}"] = discriminator_verdict(ab.original, ba.original)
        reasons[f"p{index}"] = [ab.reason, ba.reason]
    distinguishable = [page for page, verdict in verdicts.items() if verdict == "distinguishable"]
    return {"pages": len(verdicts), "distinguishable": len(distinguishable),
            "distinguishable_pages": distinguishable, "reasons": reasons}


def v33_output_checks(source_pdf, output_pdf, placed_templates, style, cfg, vision=None,
                      source_pages: list[Path] | None = None, output_pages: list[Path] | None = None) -> dict:
    """Every v3.3 output check, with its gates. Each check that cannot run says why instead of passing."""
    out: dict = {"gates": {}, "failures": [], "manual_review": []}
    try:
        # E-56: box-wise (same reader on source and output, per layout box); alignment / overlap are
        # still measured on the re-read layout, for information
        from src.rendering._structure import boxwise_checks
        reread = structure_checks(output_pdf, placed_templates, style, cfg, source_pdf)
        structure = boxwise_checks(source_pdf, output_pdf, placed_templates, cfg.qa.min_block_iou)
        structure.update(alignment=reread["alignment"], overlap=reread["overlap"])
        out.update({k: structure[k] for k in ("roundtrip", "iou", "text_placement", "alignment", "overlap")})
        out["gates"]["roundtrip_structure"] = not structure["roundtrip"]
        out["gates"]["layout_metrics"] = not structure["layout_failures"]
        out["failures"] += [f"roundtrip: {e}" for e in structure["roundtrip"]]
        out["failures"] += [f"layout_metrics: {e}" for e in structure["layout_failures"]]
    except Exception as error:
        out["gates"]["roundtrip_structure"] = out["gates"]["layout_metrics"] = False
        out["failures"].append(f"roundtrip: could not re-read the output ({type(error).__name__}: {error})")
    charts = chart_style_checks(source_pdf, output_pdf, placed_templates, cfg.qa.chart_style_min)
    out["chart_style"] = charts["scores"]
    out["gates"]["chart_style_similar"] = not charts["failures"]
    out["failures"] += [f"chart_style_similar: {e}" for e in charts["failures"]]
    if vision is not None and source_pages and output_pages:
        verdict = discriminator(source_pages, output_pages, vision)
        out["discriminator"] = verdict
        share = verdict["distinguishable"] / verdict["pages"] if verdict["pages"] else 0.0
        out["gates"]["discriminator"] = share <= cfg.qa.max_distinguishable_share
        out["manual_review"] += [f"{page}: the vision model told the output from the source "
                                 f"({'; '.join(verdict['reasons'].get(page, []))})"
                                 for page in verdict["distinguishable_pages"]]
    else:
        out["discriminator"] = {"skipped": "no vision model"}
    return out
