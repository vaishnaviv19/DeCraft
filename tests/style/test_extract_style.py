# tests/style/test_extract_style.py
from pathlib import Path

import pymupdf
import pytest

from src.config import load_config
from src.style import extract_style
from src.style._extract import clean_font_name, fallback_for

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "input" / "sample_input_1.pdf"


def clean_name():
    assert clean_font_name("ABCDEF+Calibri-Bold") == ("Calibri", True, False)


def test_clean_font_name_strips_prefix_and_suffixes():
    assert clean_font_name("ABCDEF+Calibri-Bold") == ("Calibri", True, False)
    assert clean_font_name("TimesNewRoman-Italic") == ("Times New Roman", False, True)
    assert clean_font_name("Helvetica") == ("Helvetica", False, False)


def test_fallback_mapping():
    assert fallback_for("Calibri") == "Carlito"
    assert fallback_for("Baskerville") == "Liberation Serif"
    assert fallback_for("Some Grotesk") == "Liberation Sans"


def synthetic_pdf(path: Path) -> Path:
    blue = (31 / 255, 56 / 255, 100 / 255)
    document = pymupdf.open()
    for _ in range(2):
        page = document.new_page(width=595, height=842)
        page.draw_rect(pymupdf.Rect(72, 60, 300, 160), color=None, fill=blue)
        page.insert_text((72, 40), "Acme Industries - Q1 Review", fontsize=8, fontname="helv")
        page.insert_text((72, 200), "Quarterly Review", fontsize=18, fontname="hebo")
        for i in range(3):
            page.insert_text((72, 240 + 14 * i), f"Body text line {i}", fontsize=10, fontname="helv")
        page.insert_text((72, 800), "Page footer", fontsize=8, fontname="helv")
    document.save(path)
    document.close()
    return path


def test_synthetic_pdf_gives_two_styles_geometry_and_palette(tmp_path):
    profile = extract_style(synthetic_pdf(tmp_path / "style.pdf"), load_config())
    sizes = {style.size_pt for style in profile.text_styles}
    assert 18.0 in sizes and 10.0 in sizes
    bold = [style for style in profile.text_styles if style.size_pt == 18.0]
    assert bold and bold[0].font_id in {font.font_id for font in profile.fonts if font.bold}
    assert profile.text_styles[0].size_pt == 10.0                 # body = most characters
    assert profile.palette[0] == "#1F3864"                        # the blue rectangle
    assert profile.header_text and "Acme" in profile.header_text
    assert profile.footer_text and "footer" in profile.footer_text.lower()
    assert all(margin > 0 for margin in profile.margins_pt)
    assert (profile.page_width_pt, profile.page_height_pt) == (595.0, 842.0)
    assert all(font.fallback for font in profile.fonts)


def test_the_real_sample_gives_a_valid_profile():
    profile = extract_style(SAMPLE, load_config())
    assert 3 <= len(profile.text_styles) <= 25
    assert 3 <= len(profile.palette) <= 8
    assert all(margin > 0 for margin in profile.margins_pt)
    assert profile.header_text and profile.footer_text
    assert profile.table_border_pt >= 0
