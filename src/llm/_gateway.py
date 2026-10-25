# src/llm/_gateway.py
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from src.errors import LLMBudgetExceeded

T = TypeVar("T", bound=BaseModel)
FENCE = chr(96) * 3


class LLMClient(Protocol):
    model_name: str
    def complete(self, system: str, user: str, temperature: float = 0.0, max_tokens: int = 4000) -> str: ...


@dataclass(frozen=True)
class PromptSpec:
    name: str          # e.g. "annotator"
    version: str       # e.g. "v1"; bump on ANY text change so the cache key changes
    system: str


@dataclass
class CallRecord:
    prompt: str
    cache_hit: bool
    attempt: int
    ok: bool
    error: str = ""


def strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith(FENCE):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.rsplit(FENCE, 1)[0]
    return s.strip()


@dataclass
class LLMGateway:
    client: LLMClient | None
    cache_dir: Path
    enabled: bool = True
    retries: int = 3
    timeout_s: float = 45.0
    max_output_tokens: int = 4000
    stage_token_budget: int = 200_000
    log: list[CallRecord] = field(default_factory=list)
    _tokens_used: int = 0

    def ask_json(self, prompt: PromptSpec, user: str, schema: type[T]) -> T | None:
        """Return a validated `schema` instance, or None (caller falls back to the deterministic path).
        Never raises for bad model output; raises LLMBudgetExceeded only when the stage budget is spent."""
        if not self.enabled or self.client is None:
            self.log.append(CallRecord(prompt.name, False, 0, False, "llm disabled"))
            return None
        last_err = ""
        for attempt in range(1, self.retries + 1):
            hint = f"\nPrevious reply was invalid: {last_err}. Return valid JSON only." if last_err else ""
            hit = False
            try:
                raw, hit = self._complete_cached(prompt, user + hint)
                out = schema.model_validate(json.loads(strip_fences(raw)))
                self.log.append(CallRecord(prompt.name, hit, attempt, True))
                return out
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = str(e)[:300]
                self.log.append(CallRecord(prompt.name, hit, attempt, False, last_err))
            except TimeoutError:
                last_err = "timeout"
                self.log.append(CallRecord(prompt.name, False, attempt, False, last_err))
            except OSError as e:                          # model server down: same as a failed try
                last_err = f"unavailable: {e}"[:300]
                self.log.append(CallRecord(prompt.name, False, attempt, False, last_err))
        return None

    # ---- internals -------------------------------------------------------
    def _key(self, prompt: PromptSpec, user: str) -> str:
        blob = json.dumps([self.client.model_name, prompt.name, prompt.version, prompt.system, user])
        return hashlib.sha256(blob.encode()).hexdigest()

    def _complete_cached(self, prompt: PromptSpec, user: str) -> tuple[str, bool]:
        path = self.cache_dir / f"{self._key(prompt, user)}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["reply"], True
        est = (len(prompt.system) + len(user)) // 4 + self.max_output_tokens
        if self._tokens_used + est > self.stage_token_budget:
            raise LLMBudgetExceeded(f"stage budget {self.stage_token_budget} tokens exhausted")
        self._tokens_used += est
        ex = cf.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(self.client.complete, prompt.system, user, 0.0, self.max_output_tokens)
        try:
            reply = fut.result(timeout=self.timeout_s)
        except cf.TimeoutError as e:
            raise TimeoutError from e
        finally:
            ex.shutdown(wait=False, cancel_futures=True)   # never block the pipeline on a hung call
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"reply": reply}), encoding="utf-8")
        return reply, False
