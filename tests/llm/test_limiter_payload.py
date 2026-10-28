# tests/llm/test_limiter_payload.py
import pytest
from src.errors import LLMBudgetExceeded
from src.llm._limiter import Limits, RateLimiter
from src.llm._payload import estimate_tokens, fit_payload


class FakeClock:
    def __init__(self):
        self.t = 0.0
    def now(self):
        return self.t
    def sleep(self, s):
        self.t += s


def limiter(**kw):
    c = FakeClock()
    lim = Limits(**{'rpm': 30, 'tpm': 8000, 'rpd': 1000, 'tpd': 200_000, **kw})
    return RateLimiter(lim, clock=c.now, sleep=c.sleep), c


def test_tpm_forces_wait_instead_of_429():
    rl, c = limiter()
    assert rl.acquire(5000) == 0
    waited = rl.acquire(5000)          # 10,000 > 8,000 in the same minute -> must wait ~60 s
    assert 59 <= waited <= 61 and c.t >= 59


def test_rpm_limit():
    rl, c = limiter(rpm=2)
    rl.acquire(10); rl.acquire(10)
    assert rl.acquire(10) > 0


def test_daily_token_quota_stops_cleanly():
    rl, _ = limiter(tpd=9000)
    rl.acquire(5000)
    with pytest.raises(LLMBudgetExceeded, match='daily'):
        rl.acquire(5000)


def test_oversized_single_call_is_rejected():
    rl, _ = limiter()
    with pytest.raises(LLMBudgetExceeded, match='shrink'):
        rl.acquire(9000)


def test_fit_payload_drops_lowest_priority_first():
    req = {'task': 'write', 'sec': 'S1'}
    opt = [('facts', ['f'] * 20), ('summaries', ['x' * 400] * 10)]
    text, dropped = fit_payload(req, opt, max_tokens=200)
    assert dropped == ['summaries'] and '"facts"' in text and estimate_tokens(text) <= 200


def test_fit_payload_refuses_oversized_required_part():
    with pytest.raises(ValueError):
        fit_payload({'t': 'x' * 2000}, [], max_tokens=100)


def test_the_limiter_is_safe_with_parallel_callers():
    """Live run 2026-10-26: two writer threads crashed it with 'deque mutated during iteration'."""
    from concurrent.futures import ThreadPoolExecutor
    from src.llm import Limits, RateLimiter
    limiter = RateLimiter(Limits(rpm=10_000, tpm=10_000_000, rpd=10_000_000, tpd=10_000_000_000))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: limiter.acquire(10), range(4000)))
    assert len(limiter._minute) == 4000 and len(limiter._day) == 4000
