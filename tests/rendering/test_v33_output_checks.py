# tests/rendering/test_v33_output_checks.py -- F-08 round trip, F-09 realism metrics, F-10 css, F-11 repeats
from types import SimpleNamespace as NS

from src.narrative._repeats import cross_section_repeats
from src.rendering._css import detect_language, text_css
from src.rendering._structure import (alignment_score, compare, discriminator_verdict, mean_block_iou,
                                      overlap_score, signature)

SRC = [[("heading", (50, 40, 300, 60), None), ("text", (50, 70, 550, 200), None),
        ("table", (50, 220, 550, 400), 4), ("chart", (50, 420, 550, 600), None)]]


def test_round_trip_identical_structure_passes():
    assert compare(signature(SRC), signature(SRC)) == []


def test_round_trip_detects_lost_table_column_and_extra_block():
    out = [[("heading", (50, 40, 300, 60), None), ("text", (50, 70, 550, 200), None),
            ("table", (50, 220, 550, 400), 3), ("chart", (50, 420, 550, 600), None),
            ("photo", (50, 620, 550, 700), None)]]
    errs = compare(signature(SRC), signature(out))
    assert any("table column" in e for e in errs) and any("photo count" in e for e in errs)


def test_text_split_by_one_block_is_tolerated():
    out = [SRC[0][:2] + [("text", (50, 205, 550, 215), None)] + SRC[0][2:]]
    assert compare(signature(SRC), signature(out), min_order_sim=0.8) == []


def test_layout_metrics():
    src = {"b1": (50, 40, 300, 60), "b2": (50, 70, 550, 200)}
    assert mean_block_iou(src, src) == 1.0 and mean_block_iou(src, {"b1": (50, 40, 300, 60)}) == 0.5
    aligned = [(50, 0, 200, 10), (50, 20, 300, 30)]
    ragged = [(50, 0, 200, 10), (73, 20, 311, 30)]
    assert alignment_score(aligned) < alignment_score(ragged)
    assert overlap_score(aligned) == 0 and overlap_score([(0, 0, 10, 10), (5, 5, 15, 15)]) > 0


def test_discriminator_needs_both_orders():
    assert discriminator_verdict("A", "B") == "distinguishable"
    assert discriminator_verdict("A", "A") == "indistinguishable"


def test_justified_text_gets_hyphenation_font_size_untouched():
    css = text_css(NS(size_pt=9.5, line_height=1.3, color="#222222", align="justify"), "en")
    assert "hyphens:auto" in css and "font-size:9.5pt" in css
    assert "hyphens" not in text_css(NS(size_pt=9.5, line_height=1.3, color="#222", align="left"), "en")


def test_language_defaults_to_english():
    assert detect_language("") == "en"
    assert detect_language("Consolidated revenue grew on the back of higher volumes in the quarter.") == "en"


def test_cross_section_repetition():
    sections = {"S1": "Revenue {{rev.trend|word}} to {{rev.total|auto}} on strong demand across all regions.",
                "S3": "Revenue {{rev.trend|word}} to {{rev.total|auto}} on strong demand across most regions.",
                "S4": "The new campus in Hosur doubled assembly capacity for the industrial segment."}
    hits = cross_section_repeats(sections)
    assert len(hits) == 1 and {hits[0][0], hits[0][2]} == {"S1", "S3"}
