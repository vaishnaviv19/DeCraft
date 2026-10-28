# tests/core/test_units.py
import pytest
from src.core.units import parse_scale, resolve_scale


@pytest.mark.parametrize("text,multiplier", [
    ("Revenue ($ millions)", 1e6),
    ("in $bn", 1e9),
    ("(Rs. cr)", 1e7),
    ("Rs. crore", 1e7),
    ("$'000s", 1e3),
    ("INR lakh", 1e5),
    ("Revenue", 1.0),
    ("2023", 1.0),
])
def test_parse_scale(text, multiplier):
    assert parse_scale(text).multiplier == multiplier


def test_header_beats_caption_and_conflict_warns():
    scale, warnings = resolve_scale(["Revenue ($ millions)", "All figures in $ billions"])
    assert scale.multiplier == 1e6
    assert "millions" in scale.evidence
    assert warnings and "conflict" in warnings[0]


def test_dollar_millions_and_billion_text_reconcile():
    """The same figure in two tables must land on the same base unit."""
    header_scale = parse_scale("Revenue ($ millions)")
    text_scale = parse_scale("Revenue of $1.25 billion")
    assert header_scale.multiplier == 1e6 and text_scale.multiplier == 1e9


def test_no_scale_source_keeps_values():
    scale, warnings = resolve_scale(["Revenue", "Cost"])
    assert scale.multiplier == 1.0 and warnings == []

