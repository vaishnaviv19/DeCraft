# tests/narrative/test_placeholders.py
import pytest

from src.contracts import Fact
from src.narrative._placeholders import (StrayNumber, UnknownPlaceholder, placeholder_keys, resolve, stray_numbers)

FACTS = {
    "rev": Fact(key="rev", value=1234.0, kind="number", source="t.rev:sum"),
    "pct": Fact(key="pct", value=12.34, kind="number", unit="%", source="t.pct"),
    "name": Fact(key="name", value="North", kind="text", source="t.name"),
    "dir": Fact(key="dir", value="up", kind="word", source="pattern:p1"),
}


def test_number_formats():
    assert resolve("Revenue was {{ rev|:0 }}.", FACTS) == "Revenue was 1,234."
    assert resolve("Margin {{ pct|%:1 }}.", FACTS) == "Margin 12.3%."


def test_text_and_word_placeholders():
    assert resolve("{{ name|text }}", FACTS) == "North"
    assert resolve("The trend is {{ dir|word }}.", FACTS) == "The trend is up."


def test_unknown_key_raises():
    with pytest.raises(UnknownPlaceholder):
        resolve("{{ ghost|:0 }}", FACTS)


def test_a_digit_outside_a_placeholder_is_rejected():
    assert stray_numbers("Revenue was {{ rev|:0 }} in 2024") == ["2", "0", "2", "4"]
    with pytest.raises(StrayNumber):
        resolve("Revenue was {{ rev|:0 }} in 2024", FACTS)




def test_placeholder_keys():
    assert placeholder_keys("{{ a|:0 }} and {{ b }}") == ["a", "b"]
