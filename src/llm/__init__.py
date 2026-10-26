# src/llm/__init__.py  -- PUBLIC INTERFACE of the llm module (deep module)
"""Everything that talks to a language model goes through LLMGateway.ask_json.

Hidden inside: prompt versioning and per-prompt budgets, response cache (with `cache_only`), retries
with a repair hint, code-fence stripping, strict schema validation, timeouts, token budgets, the
client-side RateLimiter, the JSONL prompt log, disabled mode and the call log.
"""
from __future__ import annotations

import os
from pathlib import Path

from src.config import Config
from src.llm._clients import LLMUnavailable, OllamaClient, default_gateway
from src.llm._gateway import CallRecord, LLMClient, LLMGateway, PromptSpec
from src.llm._limiter import Limits, RateLimiter
from src.llm._payload import estimate_tokens, fit_payload
from src.llm._providers import CloudflareImageClient, CloudflareVisionClient, GroqClient

__all__ = ["LLMClient", "LLMGateway", "PromptSpec", "CallRecord", "OllamaClient", "LLMUnavailable",
           "default_gateway", "Limits", "RateLimiter", "estimate_tokens", "fit_payload",
           "GroqClient", "CloudflareVisionClient", "CloudflareImageClient", "make_gateways"]


def _limits(settings) -> Limits:
    return Limits(**settings.limits.model_dump())


def make_gateways(cfg: Config) -> dict[str, object]:
    """One gateway per provider (E-01, E-04).

    Keys come only from the environment; a missing key leaves that gateway disabled instead of
    failing the Run. `DECRAFT_CACHE_ONLY=1` makes every gateway serve cache hits and raise CacheMiss
    otherwise (E-07). All calls are logged to `artifacts/prompt_log.jsonl` (E-24).
    """
    root = Path(cfg.artifacts_dir)
    cache_only = os.environ.get("DECRAFT_CACHE_ONLY") == "1"
    log_path = root / "prompt_log.jsonl"

    text, text_kind, text_client = cfg.providers.text, cfg.providers.text.kind, None
    if text.kind == "ollama":                                      # E-33: the local model as the main one
        text_client = OllamaClient(model=text.model, timeout_s=cfg.budgets.llm_timeout_seconds)
        text_client = text_client if text_client.available() else None
    elif os.environ.get("GROQ_API_KEY"):
        text_client = GroqClient(text.model, cfg.budgets.llm_timeout_seconds)
    elif text.fallback == "ollama":                                # no key: the local model, if it answers
        local = OllamaClient(model=text.fallback_model, timeout_s=cfg.budgets.llm_timeout_seconds)
        if local.available():
            text_client, text_kind = local, "ollama"
    vision_client = None
    if os.environ.get("CF_ACCOUNT_ID") and os.environ.get("CF_API_TOKEN"):
        vision_client = CloudflareVisionClient(cfg.providers.vision.model,
                                               cfg.budgets.llm_timeout_seconds * 2)
    image_client = None
    if os.environ.get("CF_ACCOUNT_ID") and os.environ.get("CF_API_TOKEN"):
        image_client = CloudflareImageClient(cfg.providers.image.model)

    def gateway(client, settings, kind=None) -> LLMGateway:
        made = LLMGateway(
            client=client, cache_dir=root / "llm_cache" / (kind or settings.kind),
            enabled=client is not None, retries=cfg.thresholds.llm_retries,
            timeout_s=cfg.budgets.llm_timeout_seconds,
            stage_token_budget=cfg.budgets.stage_token_budget,
            limiter=RateLimiter(_limits(settings)), cache_only=cache_only, log_path=log_path)
        made.provider = (kind or settings.kind) if client is not None else None   # shown by the CLI
        return made

    return {"text": gateway(text_client, cfg.providers.text, text_kind),
            "vision": gateway(vision_client, cfg.providers.vision),
            "image": image_client}
