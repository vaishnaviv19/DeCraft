# tests/llm/test_gateway.py  (interface tests of the llm module)
import pytest
from pydantic import BaseModel
from src.errors import LLMBudgetExceeded
from src.llm import PromptSpec
from tests.conftest import FakeLLM

P = PromptSpec('t', 'v1', 'system text')


class Out(BaseModel):
    x: int


def test_valid_json_is_parsed(gateway_for):
    assert gateway_for(FakeLLM(['{"x": 1}'])).ask_json(P, 'u', Out).x == 1


def test_fenced_json_is_accepted(gateway_for):
    fence = chr(96) * 3
    assert gateway_for(FakeLLM([fence + 'json\n{"x": 2}\n' + fence])).ask_json(P, 'u', Out).x == 2


def test_retry_sends_repair_hint_then_succeeds(gateway_for):
    llm = FakeLLM(['not json', '{"x": 3}'])
    assert gateway_for(llm).ask_json(P, 'u', Out).x == 3
    assert len(llm.calls) == 2 and 'invalid' in llm.calls[1][1]


def test_all_retries_fail_returns_none(gateway_for):
    llm = FakeLLM(['x', '{}', '[]'])
    assert gateway_for(llm, retries=3).ask_json(P, 'u', Out) is None and len(llm.calls) == 3


def test_cache_hit_skips_second_call(gateway_for):
    llm = FakeLLM(['{"x": 1}', '{"x": 99}'])
    g = gateway_for(llm)
    assert g.ask_json(P, 'u', Out).x == g.ask_json(P, 'u', Out).x == 1 and len(llm.calls) == 1


def test_prompt_version_change_misses_cache(gateway_for):
    llm = FakeLLM(['{"x": 1}', '{"x": 2}'])
    g = gateway_for(llm)
    g.ask_json(P, 'u', Out)
    assert g.ask_json(PromptSpec('t', 'v2', 'system text'), 'u', Out).x == 2


def test_disabled_gateway_never_calls_model(gateway_for):
    llm = FakeLLM(['{"x": 1}'])
    assert gateway_for(llm, enabled=False).ask_json(P, 'u', Out) is None and llm.calls == []


def test_timeout_does_not_hang(gateway_for):
    llm = FakeLLM(['{"x": 1}'] * 3, delay_s=0.5)
    assert gateway_for(llm, timeout_s=0.05, retries=2).ask_json(P, 'u', Out) is None


def test_budget_exhaustion_raises(gateway_for):
    with pytest.raises(LLMBudgetExceeded):
        gateway_for(FakeLLM(['{"x": 1}']), stage_token_budget=10).ask_json(P, 'u', Out)


def test_cache_only_mode_raises_on_miss_and_serves_hits(gateway_for):
    from src.errors import CacheMiss
    llm = FakeLLM(['{"x": 5}'])
    gateway_for(llm).ask_json(P, 'u', Out)                      # fills the cache
    g2 = gateway_for(FakeLLM([]), cache_only=True)
    assert g2.ask_json(P, 'u', Out).x == 5
    with pytest.raises(CacheMiss):
        g2.ask_json(P, 'other', Out)


def test_input_cap_is_enforced_before_sending(gateway_for):
    small = PromptSpec('t', 'v1', 'sys', max_input_tokens=5)
    llm = FakeLLM(['{"x": 1}'])
    with pytest.raises(LLMBudgetExceeded):
        gateway_for(llm).ask_json(small, 'a long user payload ' * 10, Out)
    assert llm.calls == []


def test_prompt_log_written(gateway_for, tmp_path):
    import json as _j
    g = gateway_for(FakeLLM(['bad', '{"x": 1}']), log_path=tmp_path / 'log.jsonl')
    g.ask_json(P, 'u', Out)
    recs = [_j.loads(l) for l in (tmp_path / 'log.jsonl').read_text().splitlines()]
    assert [r['ok'] for r in recs] == [False, True] and recs[0]['version'] == 'v1'
