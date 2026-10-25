# tests/core/test_units.py
import pytest
from src.core.units import ScaleInfo, from_base, parse_scale, resolve_scale, to_base


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
    assert text_scale.multiplier == 1e9
    assert to_base(1250.0, header_scale) == to_base(1.25, text_scale) == 1.25e9


def test_to_from_base_round_trip():
    scale = ScaleInfo(1e6, "($ millions)")
    assert from_base(to_base(12.5, scale), scale) == 12.5


def test_no_scale_source_keeps_values():
    scale, warnings = resolve_scale(["Revenue", "Cost"])
    assert scale.multiplier == 1.0 and warnings == []


def test_context_scale_needs_a_currency_marker():
    """Prose that happens to contain a lone scale letter is not a scale statement."""
    from src.core.units import parse_context_scale
    assert parse_context_scale("Y.E March (Rs. cr)").multiplier == 1e7
    assert parse_context_scale("Revenue of $1.25 billion").multiplier == 1e9
    assert parse_context_scale("a plain sentence with m in it").multiplier == 1.0
