# src/llm/_gateway.py
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from src.errors import CacheMiss, LLMBudgetExceeded
from src.llm._payload import estimate_tokens

T = TypeVar("T", bound=BaseModel)
FENCE = chr(96) * 3


class LLMClient(Protocol):
    model_name: str
    def complete(self, system: str, user: str, temperature: float = 0.0, max_tokens: int = 4000,
                 images: list[bytes] = (), reasoning_effort: str | None = None) -> str: ...


@dataclass(frozen=True)
class PromptSpec:
    name: str                       # e.g. "annotator"
    version: str                    # e.g. "v1"; bump on ANY text change so the cache key changes
    system: str
    temperature: float = 0.0
    max_input_tokens: int = 3500    # payload cap (system + user), enforced before sending
    max_output_tokens: int = 1200   # includes reasoning tokens for reasoning models
    reasoning_effort: str | None = "low"
    role: str = "text"              # 'text' -> text provider, 'vision' -> vision provider
    rationale: str = ""             # why these settings; copied into PROMPT_LOG.md


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
    limiter: object | None = None           # src.llm._limiter.RateLimiter (one per provider)
    cache_only: bool = False                # notebook re-run without keys: cache miss -> CacheMiss
    log_path: Path | None = None            # JSONL prompt log consumed by scripts/build_prompt_log.py
    _tokens_used: int = 0

    def start_stage(self, stage: str) -> None:
        """Open a new Pipeline Stage: each Stage gets its own token budget (M9.1)."""
        self._tokens_used = 0
        self.stage = stage

    def ask_json(self, prompt: PromptSpec, user: str, schema: type[T], images: list[bytes] | None = None) -> T | None:
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
                raw, hit = self._complete_cached(prompt, user + hint, images or [])
                out = schema.model_validate(json.loads(strip_fences(raw)))
                self.log.append(CallRecord(prompt.name, hit, attempt, True))
                self._write_log(prompt, attempt, True, "", hit)
                return out
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = str(e)[:300]
                self.log.append(CallRecord(prompt.name, hit, attempt, False, last_err))
                self._write_log(prompt, attempt, False, last_err, hit)
            except TimeoutError:
                last_err = "timeout"
                self.log.append(CallRecord(prompt.name, False, attempt, False, last_err))
            except Exception as error:                 # the model server is down: deterministic path
                from src.llm._clients import LLMUnavailable   # imported here to avoid a cycle
                if not isinstance(error, LLMUnavailable):
                    raise
                last_err = f"unavailable: {error}"[:300]
                self.log.append(CallRecord(prompt.name, False, attempt, False, last_err))
                self._write_log(prompt, attempt, False, last_err, False)
        return None

    # ---- internals -------------------------------------------------------
    def _write_log(self, prompt: PromptSpec, attempt: int, ok: bool, error: str, hit: bool) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"prompt": prompt.name, "version": prompt.version, "model": getattr(self.client, "model_name", None),
               "temperature": prompt.temperature, "reasoning_effort": prompt.reasoning_effort,
               "attempt": attempt, "ok": ok, "error": error, "cache_hit": hit}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def _key(self, prompt: PromptSpec, user: str, images: list[bytes] = ()) -> str:
        img = [hashlib.sha256(b).hexdigest() for b in images]
        blob = json.dumps([self.client.model_name, prompt.name, prompt.version, prompt.system, user, img])
        return hashlib.sha256(blob.encode()).hexdigest()

    def _complete_cached(self, prompt: PromptSpec, user: str, images: list[bytes]) -> tuple[str, bool]:
        path = self.cache_dir / f"{self._key(prompt, user, images)}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["reply"], True
        if self.cache_only:
            raise CacheMiss(f"{prompt.name}/{prompt.version}: no cached reply (cache-only mode)")
        in_tok = estimate_tokens(prompt.system + user) + 1100 * len(images)   # ~1,100 tokens per <=1024 px image
        if in_tok > prompt.max_input_tokens:
            raise LLMBudgetExceeded(f"{prompt.name}: {in_tok} input tokens > cap {prompt.max_input_tokens}")
        est = in_tok + prompt.max_output_tokens
        if self._tokens_used + est > self.stage_token_budget:
            raise LLMBudgetExceeded(f"stage budget {self.stage_token_budget} tokens exhausted")
        if self.limiter is not None:
            self.limiter.acquire(est)
        self._tokens_used += est
        ex = cf.ThreadPoolExecutor(max_workers=1)
        fut = ex.submit(self.client.complete, prompt.system, user, prompt.temperature, prompt.max_output_tokens,
                        images=images, reasoning_effort=prompt.reasoning_effort)
        try:
            reply = fut.result(timeout=self.timeout_s)
        except cf.TimeoutError as e:
            raise TimeoutError from e
        finally:
            ex.shutdown(wait=False, cancel_futures=True)   # never block the pipeline on a hung call
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"reply": reply}), encoding="utf-8")
        return reply, False
