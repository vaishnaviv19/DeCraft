# src/rendering/_structure.py   (v3.3 F-08 round trip, F-09 layout realism metrics)
"""Round trip: run OUR OWN extractor (style.build_templates) on report_output.pdf and compare its structure with
the source templates. Realism: block IoU with the source, alignment and overlap scores (LayoutGAN/LayoutNUWA
style metrics) relative to the source's own values."""
from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher

import numpy as np

CONTENT = ("heading", "text", "table", "chart", "photo", "logo", "caption")


def signature(pages: list[list[tuple[str, tuple, int | None]]]) -> dict:
    """pages: per page a list of (kind, bbox, table_columns|None) in reading order."""
    return {"pages": len(pages),
            "kinds": [dict(Counter(k for k, _, _ in p if k in CONTENT)) for p in pages],
            "order": [[k for k, _, _ in p if k in CONTENT] for p in pages],
            "table_cols": [[c for k, _, c in p if k == "table"] for p in pages]}


def compare(src: dict, out: dict, min_order_sim: float = 0.9) -> list[str]:
    """Empty list = structurally equivalent. Text blocks may split/merge by one (wrapping differs)."""
    errs = []
    if src["pages"] != out["pages"]:
        return [f"page count {out['pages']} != source {src['pages']}"]
    for i, (a, b) in enumerate(zip(src["kinds"], out["kinds"]), 1):
        for k in set(a) | set(b):
            tol = 1 if k in ("text", "caption") else 0
            if abs(a.get(k, 0) - b.get(k, 0)) > tol:
                errs.append(f"p{i}: {k} count {b.get(k, 0)} vs source {a.get(k, 0)}")
    for i, (a, b) in enumerate(zip(src["order"], out["order"]), 1):
        sim = SequenceMatcher(a=a, b=b).ratio()
        if sim < min_order_sim:
            errs.append(f"p{i}: reading order similarity {sim:.2f} < {min_order_sim}")
    for i, (a, b) in enumerate(zip(src["table_cols"], out["table_cols"]), 1):
        if a != b:
            errs.append(f"p{i}: table column counts {b} vs source {a}")
    return errs


def _iou(a, b):
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def mean_block_iou(src_boxes: dict[str, tuple], out_boxes: dict[str, tuple]) -> float:
    """Boxes keyed by block_id (output blocks keep source ids). Missing output block counts as 0."""
    if not src_boxes:
        return 1.0
    return float(np.mean([_iou(b, out_boxes[k]) if k in out_boxes else 0.0 for k, b in src_boxes.items()]))


def alignment_score(boxes: list[tuple]) -> float:
    """Mean over blocks of the smallest distance (pt) between any of its left/centre/right x-coordinates and those
    of another block. Lower = better aligned. Compare output against the SOURCE's own value."""
    if len(boxes) < 2:
        return 0.0
    xs = [(b[0], (b[0] + b[2]) / 2, b[2]) for b in boxes]
    out = []
    for i, a in enumerate(xs):
        out.append(min(abs(p - q) for j, b in enumerate(xs) if j != i for p in a for q in b))
    return float(np.mean(out))


def overlap_score(boxes: list[tuple]) -> float:
    """Sum of pairwise intersection areas / total block area (0 = no overlaps)."""
    tot = sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes) or 1.0
    inter = 0.0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            inter += max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return inter / tot


def discriminator_verdict(picked_original_ab: str, picked_original_ba: str) -> str:
    """2AFC test run twice with swapped order (cancels position bias). 'A'/'B' = image the model calls original.
    Order AB = (source, output); order BA = (output, source).
    -> 'distinguishable' (found the source both times), 'indistinguishable' (wrong or inconsistent)."""
    return "distinguishable" if picked_original_ab == "A" and picked_original_ba == "B" else "indistinguishable"


STRUCTURAL = ("heading", "table", "chart", "photo", "logo")
TEXTUAL = ("text", "caption")


def _pages(templates, kinds=STRUCTURAL) -> list[list[tuple[str, tuple, int | None]]]:
    pages = []
    for template in templates:
        blocks = sorted(template.blocks, key=lambda b: (round(b.bbox[1], 1), b.bbox[0]))
        pages.append([(b.kind, tuple(b.bbox), None) for b in blocks if b.kind in kinds])
    return pages


def _signature(pages) -> dict:
    sig = signature(pages)
    sig["table_cols"] = [[] for _ in pages]     # column counts are not re-read from rendered HTML tables
    return sig


def _inside(inner, outer, slack: float = 3.0) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] - slack <= cx <= outer[2] + slack and outer[1] - slack <= cy <= outer[3] + slack


def roundtrip_and_metrics(source_templates, output_templates, mirror_pages: set[int],
                          min_order_sim: float = 0.9, min_iou: float = 0.9,
                          max_alignment_worse: float = 2.0, max_overlap_worse: float = 0.01) -> dict:
    """F-08 + F-09 (E-50). Our extractor splits RENDERED text into different pieces than the source
    (a box of wrapped lines comes back as several blocks, an empty box as none), so:
    * the round trip compares the STRUCTURAL kinds (heading, table, chart, photo, logo) by count and
      order, headings +/-1;
    * text is checked by placement: the share of re-read text pieces whose centre lies inside a text,
      caption or heading box of the intended layout (>= min_iou), instead of a piece-by-piece IoU;
    * the block IoU is taken over the structural blocks of mirror pages."""
    src_sig, out_sig = _signature(_pages(source_templates)), _signature(_pages(output_templates))
    errors = []
    if src_sig["pages"] != out_sig["pages"]:
        errors.append(f"page count {out_sig['pages']} != source {src_sig['pages']}")
    else:
        for i, (a, b) in enumerate(zip(src_sig["kinds"], out_sig["kinds"]), 1):
            for k in set(a) | set(b):
                tol = 1 if k == "heading" else 0
                if abs(a.get(k, 0) - b.get(k, 0)) > tol:
                    errors.append(f"p{i}: {k} count {b.get(k, 0)} vs intended {a.get(k, 0)}")
        for i, (a, b) in enumerate(zip(src_sig["order"], out_sig["order"]), 1):
            sim = SequenceMatcher(a=a, b=b).ratio() if (a or b) else 1.0
            if sim < min_order_sim:
                errors.append(f"p{i}: reading order similarity {sim:.2f} < {min_order_sim}")
    iou, placement, align, overlap, failures = {}, {}, {}, {}, []
    out_by_page = {t.page: t for t in output_templates}
    for src in source_templates:
        out = out_by_page.get(src.page)
        if out is None:
            continue
        structural = lambda t: [b.bbox for b in t.blocks if b.kind in STRUCTURAL]    # noqa: E731
        a_src, a_out = alignment_score(structural(src)), alignment_score(structural(out))
        o_src, o_out = overlap_score(structural(src)), overlap_score(structural(out))
        align[f"p{src.page}"] = {"src": round(a_src, 2), "out": round(a_out, 2)}
        overlap[f"p{src.page}"] = {"src": round(o_src, 3), "out": round(o_out, 3)}
        if a_out > a_src + max_alignment_worse:
            failures.append(f"p{src.page}: alignment {a_out:.1f} pt vs intended {a_src:.1f}")
        if o_out > o_src + max_overlap_worse:
            failures.append(f"p{src.page}: overlap {o_out:.3f} vs intended {o_src:.3f}")
        boxes = [b.bbox for b in src.blocks if b.kind in TEXTUAL + ("heading",)]
        structural_boxes = [b.bbox for b in src.blocks if b.kind in ("table", "chart", "photo", "logo")]
        pieces = [b.bbox for b in out.blocks if b.kind in TEXTUAL + ("heading",)
                  and not any(_inside(b.bbox, box) for box in structural_boxes)]   # cell/axis text
        share = sum(1 for p in pieces if any(_inside(p, box) for box in boxes)) / len(pieces) if pieces else 1.0
        placement[f"p{src.page}"] = round(share, 3)
        if share < min_iou:
            failures.append(f"p{src.page}: {share:.0%} of the text sits in its intended boxes (< {min_iou:.0%})")
        if src.page in mirror_pages:
            matched = {}
            for block in src.blocks:
                if block.kind not in STRUCTURAL:
                    continue
                candidates = [b for b in out.blocks if b.kind == block.kind]
                best = max(candidates, key=lambda b: _iou(block.bbox, b.bbox), default=None)
                if best is not None:
                    matched[block.block_id] = best.bbox
            src_boxes = {b.block_id: b.bbox for b in src.blocks if b.kind in STRUCTURAL}
            value = mean_block_iou(src_boxes, matched)
            iou[f"p{src.page}"] = round(value, 3)
            if value < min_iou:
                failures.append(f"p{src.page}: mean block IoU {value:.2f} < {min_iou}")
    return {"roundtrip": errors, "iou": iou, "text_placement": placement, "alignment": align,
            "overlap": overlap, "layout_failures": failures}
