# tests/narrative/test_writer_payload.py  (live run 2026-10-26: every section went over the input cap)
import json

from src.llm import estimate_tokens
from src.narrative._writer import SECTION_PROMPT, fitted_prompt


def test_a_large_fact_list_is_cut_to_the_cap_with_the_sections_own_facts_first():
    keys = [f"table_{t:03d}.row:A rather long line item name number {i} with a long qualifier ({'x' * 60}).latest"
            for t in range(1, 13) for i in range(10)]
    section = {"title": "Review", "purpose": "x", "target_word_count": 300, "tables": [{"table_id": "table_007"}]}
    user = fitted_prompt(section, keys)
    assert estimate_tokens(SECTION_PROMPT.system + user) <= SECTION_PROMPT.max_input_tokens
    sent = [f["key"] for f in json.loads(user)["facts"]]
    assert sent[:10] == [k for k in keys if k.startswith("table_007.")]
    assert 10 < len(sent) < len(keys)


def test_a_draft_with_an_invented_key_is_sent_back_with_the_error(gateway_for):
    from src.config import load_config
    from src.narrative._writer import write_section
    from tests.conftest import FakeLLM
    bad = json.dumps({"text": "Sales were {{ t.Sales.moved }} this year."})
    good = json.dumps({"text": "Sales reached {{ t.Sales.total }} this year."})
    llm = FakeLLM([bad, good])
    out = write_section({"title": "Review"}, ["t.Sales.total"], load_config(llm_enabled=True), gateway_for(llm))
    assert out == "Sales reached {{ t.Sales.total }} this year."
    second = json.loads(llm.calls[1][1])
    assert "t.Sales.moved" in second["errors"][0]


def test_backslash_escaped_spaces_in_keys_are_accepted(gateway_for):
    from src.config import load_config
    from src.narrative._writer import write_section
    from tests.conftest import FakeLLM
    reply = json.dumps({"text": "Margin was {{ t.row:EBITDA\ Margin\ (%).latest }}."})
    out = write_section({"title": "Review"}, ["t.row:EBITDA Margin (%).latest"], load_config(llm_enabled=True),
                        gateway_for(FakeLLM([reply])))
    assert out == "Margin was {{ t.row:EBITDA Margin (%).latest }}."
