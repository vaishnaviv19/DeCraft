# tests/rendering/test_images.py
import json
from pathlib import Path

from src.config import load_config
from src.contracts.report import Block, PageTemplate, StyleProfile, TextStyle
from src.rendering._images import (build_prompt, generate_slot, image_size, photo_slots,
                                   run_images, simplify_caption)

CFG = load_config(llm_enabled=False)
STYLE = StyleProfile(page_width_pt=595, page_height_pt=842, margins_pt=(40, 40, 40, 40),
                     fonts=[], text_styles=[], palette=["#112233", "#445566", "#778899"])


class FakeImage:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, seed, width=None, height=None, steps=4):
        self.calls.append({"prompt": prompt, "seed": seed, "width": width, "height": height})
        return b"\xff\xd8fake-jpeg"


class FakeVision:
    enabled = True

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def ask_json(self, prompt, user, schema, images=None):
        self.calls.append({"name": prompt.name, "payload": json.loads(user), "images": images})
        return schema.model_validate(self.replies.pop(0))


def slot():
    return {"index": 0, "page": 2, "block_id": "p2b02", "figure_id": "p2b02",
            "bbox": (100, 100, 300, 250), "caption_block_id": "p2b03",
            "caption": "A team meeting in a bright office with a red banner.",
            "description": "wide shot, natural light"}


def test_a_match_on_the_first_try_generates_once():
    image, vision = FakeImage(), FakeVision([{"matches": True}])
    out = generate_slot(slot(), STYLE, CFG, image, vision, "annual report")
    assert len(image.calls) == 1 and out["checks"] == 1 and out["passed"]
    assert out["caption"] == slot()["caption"] and not out["simplified"]
    prompt = image.calls[0]["prompt"]
    assert prompt.startswith("A team meeting in a bright office with a red banner.")
    assert "Colour palette: #112233, #445566, #778899" in prompt
    assert "no text, no logos, no watermarks" in prompt
    assert image.calls[0]["width"] <= 1024 and image.calls[0]["width"] % 8 == 0


def test_two_misses_then_a_match_generates_three_times():
    image = FakeImage()
    vision = FakeVision([{"matches": False, "missing": ["red banner"]},
                         {"matches": False, "missing": ["crowd"]},
                         {"matches": True}])
    out = generate_slot(slot(), STYLE, CFG, image, vision, "annual report")
    assert len(image.calls) == 3 and out["passed"] and out["checks"] == 3
    assert [call["seed"] for call in image.calls] == [42, 142, 242]
    assert not out["simplified"]


def test_three_misses_simplify_the_caption_and_flag_it():
    image = FakeImage()
    vision = FakeVision([{"matches": False, "missing": ["red banner"]}] * 3)
    vision.replies.append({"text": "A team meeting."})
    out = generate_slot(slot(), STYLE, CFG, image, vision, "annual report")
    assert len(image.calls) == 3 and out["simplified"] and not out["passed"]
    assert out["caption"] == "A team meeting." and "red banner" not in out["caption"]
    assert out["missing"] == ["red banner"]


def test_a_simplified_caption_never_mentions_the_missing_elements():
    vision = FakeVision([{"text": "A team meeting under a red banner. It is bright."}])
    caption = simplify_caption(slot()["caption"], ["red banner"], vision)
    assert caption == "It is bright."


def test_a_caption_block_below_the_photo_becomes_its_caption():
    page = PageTemplate(page=1, mode="mirror", reason="", blocks=[
        Block(block_id="p1b02", kind="photo", bbox=(0, 100, 200, 250)),
        Block(block_id="p1b03", kind="caption", bbox=(0, 255, 200, 275))])
    slots = photo_slots([page], {"p1b02": {"description": "a plant"}},
                        {"p1b03": "The new plant."})
    assert slots[0]["caption"] == "The new plant." and slots[0]["caption_block_id"] == "p1b03"


def test_the_prompt_and_size_follow_the_spec():
    prompt = build_prompt(slot(), STYLE.palette, "annual report")
    assert prompt == ("A team meeting in a bright office with a red banner. Composition: wide shot, "
                      "natural light. Colour palette: #112233, #445566, #778899. Professional "
                      "editorial photograph suitable for a published annual report, natural light, "
                      "no text, no logos, no watermarks.")
    width, height = image_size((0, 0, 200, 100), 1024)
    assert (width, height) == (1024, 512)


def test_logos_are_copied_and_never_sent_to_the_image_client(tmp_path):
    image, vision = FakeImage(), FakeVision([{"matches": True}])
    logos = [(1, Block(block_id="p1b03", kind="logo", bbox=(0, 0, 20, 20)))]
    out = run_images([slot()], logos, CFG, STYLE, "annual report", image, vision, tmp_path,
                     "source.pdf", logo_reader=lambda *args: b"logo-bytes")
    assert len(image.calls) == 1 and out["generated"] == 1 and out["checks_passed"] == 1
    assert len(out["logos"]) == 1 and Path(out["logos"][0]["file"]).read_bytes() == b"logo-bytes"
    sent = [blob for call in vision.calls for blob in (call["images"] or [])]
    assert b"logo-bytes" not in sent
    assert Path(tmp_path / "slot_0.jpg").exists()


def test_a_photo_without_a_caption_or_description_is_skipped(tmp_path):
    image, vision = FakeImage(), FakeVision([])
    empty = {**slot(), "caption": "", "description": ""}
    out = run_images([empty], [], CFG, STYLE, "annual report", image, vision, tmp_path, "src.pdf")
    assert image.calls == [] and out["generated"] == 0 and out["skipped_slots"] == 1


def test_logo_keeps_its_transparency(tmp_path):
    """Live run 2026-10-26: a see-through footer logo came out on a black box."""
    import pymupdf
    from src.rendering._images import extract_logo
    pixels = bytes([255, 255, 255, 0] * 20 + [255, 255, 255, 255] * 20) * 20  # left half see-through
    logo = pymupdf.Pixmap(pymupdf.csRGB, 40, 20, pixels, 1)
    path = tmp_path / "logo.pdf"
    document = pymupdf.open()
    document.new_page(width=200, height=200).insert_image(pymupdf.Rect(10, 10, 50, 30),
                                                          stream=logo.tobytes("png"))
    document.save(str(path))
    document.close()
    out = pymupdf.Pixmap(extract_logo(str(path), 1, (10, 10, 50, 30)))
    assert out.alpha and out.pixel(2, 10)[-1] == 0 and out.pixel(37, 10)[-1] == 255
