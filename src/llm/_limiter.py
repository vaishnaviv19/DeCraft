# src/llm/_limiter.py
"""Client-side rate limiter for free tiers (decision E-04). Enforces requests/min, tokens/min, requests/day and
tokens/day BEFORE a call is sent, so the provider never returns 429. Windows are sliding (60 s, 24 h).
`clock` and `sleep` are injectable so tests run instantly."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from src.errors import LLMBudgetExceeded


@dataclass
class Limits:
    rpm: int
    tpm: int
    rpd: int
    tpd: int


@dataclass
class RateLimiter:
    limits: Limits
    clock: callable = time.monotonic
    sleep: callable = time.sleep
    max_wait_s: float = 600.0
    _minute: deque = field(default_factory=deque)   # (t, tokens)
    _day: deque = field(default_factory=deque)

    def _trim(self, now: float) -> None:
        while self._minute and now - self._minute[0][0] >= 60:
            self._minute.popleft()
        while self._day and now - self._day[0][0] >= 86_400:
            self._day.popleft()

    def acquire(self, tokens: int) -> float:
        """Block until a call of `tokens` (input + max output) fits every window. Returns seconds waited.
        Raises LLMBudgetExceeded when the DAILY window cannot fit it (caller stops; next run resumes from cache)
        or when a single call is larger than the per-minute limit."""
        if tokens > self.limits.tpm:
            raise LLMBudgetExceeded(f"one call needs {tokens} tokens > tpm {self.limits.tpm}; shrink the prompt")
        waited = 0.0
        while True:
            now = self.clock()
            self._trim(now)
            day_req, day_tok = len(self._day), sum(t for _, t in self._day)
            if day_req + 1 > self.limits.rpd or day_tok + tokens > self.limits.tpd:
                raise LLMBudgetExceeded("daily quota reached; stop and resume tomorrow from cache")
            min_req, min_tok = len(self._minute), sum(t for _, t in self._minute)
            if min_req + 1 <= self.limits.rpm and min_tok + tokens <= self.limits.tpm:
                self._minute.append((now, tokens))
                self._day.append((now, tokens))
                return waited
            wait = 60 - (now - self._minute[0][0]) + 0.05
            if waited + wait > self.max_wait_s:
                raise LLMBudgetExceeded("rate-limit wait exceeded max_wait_s")
            self.sleep(wait)
            waited += wait

    def record_actual(self, estimated: int, actual: int) -> None:
        """Replace the newest estimate with the provider-reported usage (keeps the windows honest)."""
        for q in (self._minute, self._day):
            if q and q[-1][1] == estimated:
                t, _ = q.pop()
                q.append((t, actual))
