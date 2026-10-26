# src/style/_overlay.py  -- M8.2.1 debug overlay: the source page with every template block boxed
"""`overlay_p<N>.png`: each source page at 110 dpi with its blocks drawn as coloured, labelled boxes, so
a person can confirm by eye that tables, charts and photos are boxed correctly."""
from __future__ import annotations

from pathlib import Path

COLOURS = {"heading": (0.85, 0.1, 0.1), "text": (0.1, 0.35, 0.85), "table": (0.0, 0.6, 0.2),
           "chart": (0.9, 0.5, 0.0), "photo": (0.6, 0.0, 0.7), "logo": (0.0, 0.6, 0.6),
           "caption": (0.5, 0.5, 0.0), "header": (0.4, 0.4, 0.4), "footer": (0.4, 0.4, 0.4),
           "page_number": (0.2, 0.2, 0.2), "decoration": (0.75, 0.75, 0.75)}


def write_overlays(pdf_path, templates, out_dir, dpi: int = 110) -> list[Path]:
    import pymupdf

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with pymupdf.open(str(pdf_path)) as source:
        document = pymupdf.open()
        document.insert_pdf(source)
        for template in templates:
            if not 1 <= template.page <= document.page_count:
                continue
            page = document[template.page - 1]
            for block in template.blocks:
                if block.kind == "decoration":
                    continue
                colour = COLOURS.get(block.kind, (0, 0, 0))
                rect = pymupdf.Rect(block.bbox)
                page.draw_rect(rect, color=colour, width=1.2)
                page.insert_text((rect.x0 + 1, max(rect.y0 - 1.5, 6)), f"{block.kind} {block.block_id}",
                                 fontsize=5.5, color=colour)
            path = out_dir / f"overlay_p{template.page}.png"
            page.get_pixmap(dpi=dpi).save(str(path))
            written.append(path)
        document.close()
    return written
