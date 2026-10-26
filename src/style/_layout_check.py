# src/style/_layout_check.py   (v3.3 F-05: second opinion from a layout-detection model)
"""DocLayout-YOLO (best detector on OmniDocBench) labels page regions. We match its boxes with our rule-based
blocks by IoU and measure agreement. The model never overrides our labels: low agreement only flags the page
(E-48 keeps every page mirrored; v3.3's 'switch to flow' is replaced by a QA flag)."""
from __future__ import annotations

from pathlib import Path

MAP = {"title": "heading", "plain text": "text", "abandon": "decoration", "figure": "figure",
       "figure_caption": "caption", "table": "table", "table_caption": "caption", "table_footnote": "text",
       "isolate_formula": "text", "formula_caption": "caption"}
COMPATIBLE = {"figure": {"chart", "photo", "logo"}, "decoration": {"header", "footer", "page_number", "decoration"},
              "text": {"text", "caption"}, "heading": {"heading", "text"}}
WEIGHTS_REPO = "juliozhao/DocLayout-YOLO-DocStructBench"
WEIGHTS_FILE = "doclayout_yolo_docstructbench_imgsz1024.pt"


def iou(a, b) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def kinds_agree(ours: str, model_label: str) -> bool:
    k = MAP.get(model_label, model_label)
    return ours == k or ours in COMPATIBLE.get(k, set())


def agreement(our_blocks: list[tuple[str, tuple]], model_boxes: list[tuple[str, tuple, float]],
              min_iou: float = 0.5, min_score: float = 0.4) -> tuple[float, list[str]]:
    """our_blocks: (kind, bbox). model_boxes: (label, bbox, score). Returns (share of our content blocks that the
    model confirms with a compatible label, list of disagreement descriptions)."""
    content = [(k, b) for k, b in our_blocks if k not in ("header", "footer", "page_number", "decoration")]
    boxes = [(l, b) for l, b, s in model_boxes if s >= min_score]
    if not content:
        return 1.0, []
    ok, issues = 0, []
    for k, b in content:
        best = max(boxes, key=lambda m: iou(b, m[1]), default=None)
        if best and iou(b, best[1]) >= min_iou and kinds_agree(k, best[0]):
            ok += 1
        else:
            issues.append(f"{k} at {tuple(round(v) for v in b)}: model says "
                          f"{best[0] if best and iou(b, best[1]) >= min_iou else 'nothing'}")
    return ok / len(content), issues


_MODEL = None


def model_available() -> bool:
    try:
        import doclayout_yolo  # noqa: F401
        return True
    except Exception:
        return False


def detect(page, weights: Path, imgsz: int = 1024) -> list[tuple[str, tuple, float]]:
    """(label, bbox in PDF points, score) for one page, on CPU, from a LOCAL weights file (the pipeline
    never downloads; scripts/fetch_layout_model.py does, once, with its sha256 recorded)."""   # pragma: no cover
    global _MODEL
    import numpy as np
    from doclayout_yolo import YOLOv10
    if _MODEL is None:
        _MODEL = YOLOv10(str(weights))
    zoom = imgsz / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=__import__("pymupdf").Matrix(zoom, zoom))
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3][:, :, ::-1].copy()
    result = _MODEL.predict(img, imgsz=imgsz, conf=0.2, device="cpu", verbose=False)[0]
    names = result.names
    out = []
    for box, cls, score in zip(result.boxes.xyxy.tolist(), result.boxes.cls.tolist(), result.boxes.conf.tolist()):
        out.append((str(names[int(cls)]), tuple(v / zoom for v in box), float(score)))
    return out


def check_templates(pdf_path, templates, weights: Path | None, min_agreement: float = 0.7) -> dict:
    """{"available": bool, "agreement": {"p1": 0.93, ...}, "flagged": [{"page", "agreement", "issues"}]}.
    Without the package or the local weights file the check is skipped and says why."""
    if not model_available():
        return {"available": False, "reason": "doclayout_yolo not installed", "agreement": {}, "flagged": []}
    if weights is None or not Path(weights).exists():
        return {"available": False, "reason": f"weights not found at {weights} (scripts/fetch_layout_model.py)",
                "agreement": {}, "flagged": []}
    import pymupdf
    scores, flagged = {}, []
    with pymupdf.open(str(pdf_path)) as document:
        for template in templates:
            page = document[template.page - 1]
            share, issues = agreement([(b.kind, b.bbox) for b in template.blocks], detect(page, Path(weights)))
            scores[f"p{template.page}"] = round(share, 2)
            if share < min_agreement:
                flagged.append({"page": template.page, "agreement": round(share, 2), "issues": issues[:10]})
    return {"available": True, "agreement": scores, "flagged": flagged}
