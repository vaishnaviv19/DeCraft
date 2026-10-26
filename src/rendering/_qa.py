# src/rendering/_qa.py
"""Rendered-PDF QA (M8.6): pages rendered, page count, overflow, placeholders, word count, captions,
chart sidecars. Anything needing human judgement becomes `manual_review_required`, never a silent pass."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.contracts.dataset import CanonicalDataset
from src.rendering._charts import chart_caption, sidecar_matches


@dataclass
class QAResult:
    ok: bool
    pages: int
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    manual_review_required: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "pages": self.pages, "checks": self.checks,
                "notes": self.notes, "manual_review_required": self.manual_review_required}


def blocks_within(page_rect, blocks, tolerance: float = 2.0) -> bool:
    """Every text block must sit inside the page, allowing a small tolerance."""
    left, top, right, bottom = page_rect
    for block in blocks:
        x0, y0, x1, y1 = block[:4]
        if x0 < left - tolerance or y0 < top - tolerance \
                or x1 > right + tolerance or y1 > bottom + tolerance:
            return False
    return True


def _contact_sheet(images: list[Path], target: Path, columns: int = 3) -> None:
    """One page of thumbnails. Skipped silently when Pillow is unavailable (the pages are kept)."""
    if not images:
        return
    try:
        from PIL import Image
    except ImportError:
        return
    thumbs = [Image.open(path).convert("RGB") for path in images]
    width = max(t.width for t in thumbs)
    height = max(t.height for t in thumbs)
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * width, rows * height), "white")
    for index, thumb in enumerate(thumbs):
        sheet.paste(thumb, ((index % columns) * width, (index // columns) * height))
    sheet.save(target)


def _squash(text: str) -> str:
    return "".join(str(text).split()).lower()


def run_qa(pdf_path: Path, data: CanonicalDataset, out_dir: Path, planned_pages: int | None = None,
           min_words: int = 1000, page_tolerance: int = 1, planned_charts: int | None = None) -> QAResult:
    import pymupdf

    document = pymupdf.open(str(pdf_path))
    pages = len(document)
    text = "\n".join(page.get_text() for page in document)
    checks: dict[str, bool] = {}

    checks["pages_rendered"] = pages > 0
    checks["page_count"] = (planned_pages is None
                            or abs(pages - planned_pages) <= page_tolerance)
    checks["no_unresolved_placeholders"] = "{{" not in text and "}}" not in text
    checks["word_count"] = len(text.split()) >= min_words
    checks["no_overflow"] = all(
        blocks_within((page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1),
                      [b[:4] for b in page.get_text("blocks")])
        for page in document)
    # every planned chart exists; each sidecar EQUALS the Display Table; each caption is on the page
    checks["charts_present"] = planned_charts is None or len(data.charts) >= planned_charts
    checks["chart_sidecars"] = all(
        sidecar_matches(data, chart_id, Path(out_dir) / "charts" / f"{chart_id}.json") for chart_id in data.charts)
    flat = _squash(text)
    checks["charts_captioned"] = all(_squash(chart_caption(spec)) in flat for spec in data.charts.values())

    pages_dir = Path(out_dir) / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    page_images = []
    for number, page in enumerate(document, start=1):
        target = pages_dir / f"page_{number:03d}.png"
        page.get_pixmap(dpi=90).save(str(target))
        page_images.append(target)
    document.close()
    _contact_sheet(page_images, Path(out_dir) / "contact_sheet.png")

    manual = ["visual similarity to the source layout needs a multimodal model or a human"]
    notes = [f"{pages} pages rendered", f"{len(text.split())} words"]
    return QAResult(ok=all(checks.values()), pages=pages, checks=checks, notes=notes,
                    manual_review_required=manual)
