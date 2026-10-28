# tests/rendering/test_fit_doc.py
import json

from src.config import load_config
from src.rendering._fit import Box
from src.rendering._fit_doc import fit_blocks

CFG = load_config(llm_enabled=False)
BOX = Box(width_pt=120, height_pt=24, font_size=10, line_height=1.2)   # 2 lines of 20 characters
WIDTH = lambda text: 6.0 * len(text)                                    # noqa: E731
LONG = "Alpha Beta reported strong growth across every region and product line this year."
SHORTER = "Alpha Beta grew across all regions."


def fit(text: str, gateway):
    texts, rows, summary = fit_blocks({"p1b01": text}, {"p1b01": BOX}, {"p1b01": WIDTH},
                                      CFG, gateway, ["FY25"])
    return texts["p1b01"], rows[0], summary


def test_a_block_that_already_fits_is_untouched(gateway_for):
    llm = __import__("tests.conftest", fromlist=["FakeLLM"]).FakeLLM([])
    text, row, summary = fit("Alpha Beta grew.", gateway_for(llm))
    assert text == "Alpha Beta grew." and row["fits"]
    assert summary["overflow_before"] == 0 and summary["shortened"] == 0 and summary["cut"] == 0
    assert llm.calls == []


def test_a_valid_shortened_text_is_accepted(gateway_for):
    llm = __import__("tests.conftest", fromlist=["FakeLLM"]).FakeLLM(
        [json.dumps({"text": SHORTER})])
    text, row, summary = fit(LONG, gateway_for(llm))
    assert text == SHORTER and row["fits"]
    assert summary["overflow_before"] == 1 and summary["shortened"] == 1 and summary["cut"] == 0


def test_a_shortened_text_that_adds_a_placeholder_is_rejected(gateway_for):
    from tests.conftest import FakeLLM

    bad = json.dumps({"text": "Alpha Beta grew {{ new.key|:0 }} across regions."})
    llm = FakeLLM([bad, json.dumps({"text": SHORTER})])
    text, row, summary = fit(LONG, gateway_for(llm))
    assert text == SHORTER and len(llm.calls) == 2 and summary["shortened"] == 1


def test_an_impossible_fit_is_cut_at_the_last_full_sentence(gateway_for):
    from tests.conftest import FakeLLM

    original = "Alpha Beta grew. The rest of this sentence is far too long to fit in this small box."
    llm = FakeLLM([json.dumps({"text": original}), json.dumps({"text": original})])
    text, row, summary = fit(original, gateway_for(llm))
    assert text == "Alpha Beta grew." and row["fits"]
    assert summary["cut"] == 1 and summary["flags"]


def test_without_a_gateway_the_text_is_cut(gateway_for):
    original = "Alpha Beta grew. This second sentence will never fit inside the box."
    text, row, summary = fit(original, None)
    assert text == "Alpha Beta grew." and row["fits"] and summary["cut"] == 1
