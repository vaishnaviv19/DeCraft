# src/rendering/_images.py
"""Photos (M8.10, E-20). Only if the source has photos.

A photo block is a slot. Its caption is written by the section writer (the caption block); when the
source has no caption block, the Figure Analyst's description is the caption. Python builds the FLUX
prompt from caption + composition + palette, generates at the slot's aspect ratio, asks the vision
model to check the result against the caption, regenerates at most twice, then simplifies the caption
to what the image shows. Logos are copied from the source bytes and never generated."""
from __future__ import annotations

import io
import json
import logging
import re
from pathlib import Path

from src.contracts.report import Block, ImageCheckOut, PageTemplate, ShortenOut, StyleProfile
from src.llm import LLMGateway
from src.narrative import SHORTEN
from src.rendering._prompts import IMAGE_CHECK

log = logging.getLogger(__name__)


def photo_slots(templates: list[PageTemplate], figures: dict[str, dict],
                texts: dict[str, str]) -> list[dict]:
    """One slot per photo block, with the nearest caption block above or below it (within 48 pt)."""
    slots: list[dict] = []
    for page in templates:
        captions = [block for block in page.blocks if block.kind == "caption"]
        for block in page.blocks:
            if block.kind != "photo":
                continue
            x0, y0, x1, y1 = block.bbox
            near = [caption for caption in captions
                    if (0 <= caption.bbox[1] - y1 <= 48 or 0 <= y0 - caption.bbox[3] <= 48)
                    and caption.bbox[0] < x1 and caption.bbox[2] > x0]
            caption_block = min(near, key=lambda item: min(abs(item.bbox[1] - y1),
                                                           abs(y0 - item.bbox[3])),
                                default=None)
            caption = (texts.get(caption_block.block_id) if caption_block else "") or ""
            figure_id = block.figure_id or block.block_id
            figure = figures.get(figure_id) or {}
            slots.append({"index": len(slots), "page": page.page, "block_id": block.block_id,
                          "figure_id": figure_id, "bbox": block.bbox,
                          "caption_block_id": caption_block.block_id if caption_block else None,
                          "caption": " ".join(caption.split()),
                          "description": " ".join((figure.get("description") or "").split())})
    return slots


def logo_slots(templates: list[PageTemplate]) -> list[tuple[int, Block]]:
    return [(page.source_page or page.page, block) for page in templates for block in page.blocks
            if block.kind == "logo"]


def build_prompt(slot: dict, palette: list[str], dt: str) -> str:
    caption = (slot.get("caption") or "An editorial photograph").rstrip(".")
    description = (slot.get("description") or "the subject of the caption").rstrip(".")
    colours = ", ".join(palette[:3]) or "#333333, #777777, #CCCCCC"
    return (f"{caption}. Composition: {description}. Colour palette: {colours}. "
            f"Professional editorial photograph suitable for a published {dt}, natural light, "
            f"no text, no logos, no watermarks.")


def image_size(bbox, max_px: int = 1024) -> tuple[int, int]:
    """The slot's aspect ratio with the longest side <= max_px and both sides multiples of 8."""
    width = max(1.0, float(bbox[2]) - float(bbox[0]))
    height = max(1.0, float(bbox[3]) - float(bbox[1]))
    scale = max_px / max(width, height)

    def snap(value: float) -> int:
        return max(64, min(max_px, int(round(value * scale / 8.0)) * 8))

    return snap(width), snap(height)


def _strip_missing(text: str, missing: list[str]) -> str:
    terms = [re.escape(term) for term in missing if term.strip()]
    if not terms:
        return text
    pattern = re.compile("|".join(terms), re.IGNORECASE)
    kept = [part for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()
            and not pattern.search(part)]
    return " ".join(kept).strip()


def simplify_caption(caption: str, missing: list[str], gateway: LLMGateway | None) -> str:
    """A shorten-style call that must not mention what the image lacks."""
    if not caption:
        return caption
    result = caption
    if gateway is not None and gateway.enabled:
        reply = gateway.ask_json(SHORTEN, json.dumps({
            "text": caption, "max_words": max(3, len(caption.split())), "missing": missing}),
            ShortenOut)
        if reply is not None:
            result = reply.text.strip()
    result = _strip_missing(result, missing) or "A photograph."
    return result


def generate_slot(slot: dict, style: StyleProfile, cfg, image_client, vision: LLMGateway | None,
                  dt: str) -> dict:
    """Generate, check, regenerate (<= image_retries), then simplify the caption; never raises."""
    prompt = build_prompt(slot, style.palette, dt)
    width, height = image_size(slot["bbox"], cfg.report.image_max_px)
    seeds = [cfg.seed + slot["index"] + 100 * k for k in range(cfg.report.image_retries + 1)]
    image, checks, missing, passed = None, 0, [], False
    for seed in seeds:
        try:
            image = image_client.generate(prompt, seed, width=width, height=height)
        except Exception as error:                         # provider refused or down: no photo here (E-43)
            log.warning("image generation failed for slot %s: %s", slot["index"], error)
            return {"slot": slot, "image": None, "seed": seed, "checks": checks, "passed": False,
                    "missing": [], "caption": slot["caption"], "simplified": False, "prompt": prompt,
                    "skipped": True, "reason": f"image provider error: {type(error).__name__}"}
        if vision is None or not vision.enabled:
            return {"slot": slot, "image": image, "seed": seed, "checks": checks, "passed": False,
                    "missing": [], "caption": slot["caption"], "simplified": False, "prompt": prompt}
        verdict = vision.ask_json(IMAGE_CHECK, json.dumps({"caption": slot["caption"]}),
                                  ImageCheckOut, images=[image])
        checks += 1
        if verdict is None or verdict.matches:
            passed = True
            break
        for item in verdict.missing:
            if item not in missing:
                missing.append(item)
    caption = slot["caption"]
    simplified = False
    if not passed and caption and missing:
        caption = simplify_caption(caption, missing, vision)
        simplified = True
    return {"slot": slot, "image": image, "seed": seeds[-1], "checks": checks, "passed": passed,
            "missing": missing, "caption": caption, "simplified": simplified, "prompt": prompt}


def save_jpeg(data: bytes, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            image.thumbnail((1024, 1024))
            image.save(path, "JPEG", quality=80)
    except Exception:                     # a fake or already-small image: keep the bytes
        path.write_bytes(data)
    return path


def extract_logo(pdf_path, page: int, bbox) -> bytes | None:
    """The original embedded image at the logo's place, else a 300 dpi crop. Never generated."""
    try:
        import fitz

        document = fitz.open(pdf_path)
        page_object = document[page - 1]
        target = fitz.Rect(bbox)
        for info in page_object.get_image_info(xrefs=True):
            rect = fitz.Rect(info["bbox"])
            if rect.get_area() and (rect & target).get_area() / rect.get_area() > 0.4 \
                    and info.get("xref"):
                image = document.extract_image(int(info["xref"]))
                if image and image.get("smask"):
                    # keep the transparency: the bare image shows a black box where the logo is
                    # see-through (live run 2026-10-26: the footer 'G' on the teal band)
                    base = fitz.Pixmap(document, int(info["xref"]))
                    if base.n - base.alpha >= 4:                     # CMYK -> RGB
                        base = fitz.Pixmap(fitz.csRGB, base)
                    if base.alpha:
                        base = fitz.Pixmap(base, 0)
                    return fitz.Pixmap(base, fitz.Pixmap(document, int(image["smask"]))).tobytes("png")
                if image and image.get("image"):
                    return image["image"]
        return page_object.get_pixmap(clip=target, dpi=300).tobytes("png")
    except Exception:
        return None


def run_images(slots: list[dict], logos: list[tuple[int, Block]], cfg, style: StyleProfile,
               dt: str, image_client, vision: LLMGateway | None, out_dir, pdf_path,
               logo_reader=None) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    if image_client is not None:
        for slot in slots:
            if not slot.get("caption") and not slot.get("description"):
                items.append({"slot": slot, "image": None, "checks": 0, "passed": False,
                              "missing": [], "caption": "", "simplified": False,
                              "skipped": True, "reason": "no caption or description"})
                continue
            item = generate_slot(slot, style, cfg, image_client, vision, dt)
            if item.get("image"):
                item["file"] = str(save_jpeg(item["image"], out_dir / f"slot_{slot['index']}.jpg"))
            items.append(item)
    else:
        items = [{"slot": slot, "image": None, "checks": 0, "passed": False, "missing": [],
                  "caption": slot["caption"], "simplified": False}
                 for slot in slots]
    saved_logos = []
    reader = logo_reader or extract_logo
    for index, (page, block) in enumerate(logos):
        data = reader(pdf_path, page, block.bbox) if reader else None
        if data:
            path = out_dir / f"logo_{index}.png"
            path.write_bytes(data)
            saved_logos.append({"block_id": block.block_id, "page": page, "file": str(path)})
    return {"photos": len(slots), "slots": len(slots), "generated": sum(1 for item in items
                                                                       if item.get("image")),
            "checks_passed": sum(1 for item in items if item["passed"]),
            "captions_simplified": sum(1 for item in items if item["simplified"]),
            "skipped_slots": sum(1 for item in items if item.get("skipped")),
            "skipped": image_client is None,
            "reason": "" if image_client is not None else "no image gateway",
            "logos": saved_logos, "items": items}
