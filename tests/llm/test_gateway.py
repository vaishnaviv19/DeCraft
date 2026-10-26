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


# ---------------------------------------------------------------- live run 2026-10-26: a 401 stopped the run
@pytest.mark.parametrize("status,enabled_after,calls", [(401, False, 1), (403, False, 1), (400, True, 1),
                                                         (503, True, 3), (429, True, 3)])
def test_provider_http_errors_fall_back_instead_of_crashing(tmp_path, status, enabled_after, calls):
    import httpx
    from pydantic import BaseModel
    from src.llm import LLMGateway, PromptSpec

    class Ok(BaseModel):
        ok: int

    class Refusing:
        model_name = "m"
        n = 0

        def complete(self, *args, **kwargs):
            self.n += 1
            request = httpx.Request("POST", "https://example.invalid")
            raise httpx.HTTPStatusError("x", request=request, response=httpx.Response(status, request=request))

    client = Refusing()
    gateway = LLMGateway(client=client, cache_dir=tmp_path, enabled=True, retries=3, timeout_s=5,
                         backoff_scale=0.0)
    assert gateway.ask_json(PromptSpec("p", "v1", "Return JSON"), "u", Ok) is None
    assert gateway.enabled is enabled_after and client.n == calls


def test_a_used_up_daily_quota_switches_the_provider_off_at_once(tmp_path):
    import httpx
    from pydantic import BaseModel
    from src.llm import LLMGateway, PromptSpec

    class Ok(BaseModel):
        ok: int

    class OutOfQuota:
        model_name = "m"
        n = 0

        def complete(self, *args, **kwargs):
            self.n += 1
            request = httpx.Request("POST", "https://example.invalid")
            text = ('{"error":{"message":"Rate limit reached for model on tokens per day (TPD): '
                    'Limit 200000, Used 198254, Requested 2681."}}')
            raise httpx.HTTPStatusError("x", request=request, response=httpx.Response(429, request=request, text=text))

    client = OutOfQuota()
    gateway = LLMGateway(client=client, cache_dir=tmp_path, enabled=True, retries=3, timeout_s=5, backoff_scale=0.0)
    assert gateway.ask_json(PromptSpec("p", "v1", "Return JSON"), "u", Ok) is None
    assert client.n == 1 and gateway.enabled is False and gateway.disabled_reason == "daily quota used up"


def _refusing_client(status: int, text: str):
    import httpx

    class Refusing:
        model_name = "m"
        n = 0

        def complete(self, *args, **kwargs):
            self.n += 1
            request = httpx.Request("POST", "https://example.invalid")
            raise httpx.HTTPStatusError("x", request=request,
                                        response=httpx.Response(status, request=request, text=text))
    return Refusing()


def test_cloudflare_used_up_neurons_switch_the_provider_off_at_once(tmp_path):
    from pydantic import BaseModel
    from src.llm import LLMGateway, PromptSpec

    class Ok(BaseModel):
        ok: int

    client = _refusing_client(429, '{"errors":[{"message":"AiError: AiError: you have used up your daily free '
                                   'allocation of 10,000 neurons, please upgrade","code":4006}]}')
    gateway = LLMGateway(client=client, cache_dir=tmp_path, enabled=True, retries=3, timeout_s=5, backoff_scale=0.0)
    assert gateway.ask_json(PromptSpec("p", "v1", "Return JSON"), "u", Ok) is None
    assert client.n == 1 and gateway.enabled is False and gateway.disabled_reason == "daily quota used up"


def test_refused_calls_do_not_use_up_the_stage_budget(tmp_path):
    from pydantic import BaseModel
    from src.llm import LLMGateway, PromptSpec

    class Ok(BaseModel):
        ok: int

    # a busy provider (429, not a daily limit): every attempt is refused and retried
    client = _refusing_client(429, '{"error":"busy"}')
    gateway = LLMGateway(client=client, cache_dir=tmp_path, enabled=True, retries=3, timeout_s=5,
                         backoff_scale=0.0, stage_token_budget=5000)
    for _ in range(10):                  # 30 refused attempts; each reserves ~4k tokens of a 5k budget
        assert gateway.ask_json(PromptSpec("p", "v1", "Return JSON"), "u", Ok) is None
    assert client.n == 30 and gateway._tokens_used == 0
