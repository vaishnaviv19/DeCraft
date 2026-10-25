# src/llm/_clients.py
"""Real model clients for LLMGateway. The gateway adds caching, retries, schema checks and budgets;
a client only sends one prompt and returns the raw text.

OllamaClient is the HTTP code of the old `src/agents/llm_client.py`, moved here so the new pipeline
can use the same local model (the old agents now call this class too). Settings come from the same
environment variables: OLLAMA_HOST (default http://localhost:11434) and DECRAFT_LLM_MODEL
(default llama3.1).
"""
from __future__ import annotations

import os
from pathlib import Path


class LLMUnavailable(OSError):
    """The model server could not be reached; the gateway treats it like a failed attempt."""


class OllamaClient:
    def __init__(self, host: str | None = None, model: str | None = None, timeout_s: float = 120.0,
                 json_mode: bool = True):
        self.json_mode = json_mode                         # gateway prompts always expect one JSON object
        self.host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.model_name = model or os.environ.get("DECRAFT_LLM_MODEL", "llama3.1")
        self.timeout_s = timeout_s

    def available(self) -> bool:
        try:
            import requests
            return requests.get(f"{self.host}/api/tags", timeout=2).status_code == 200
        except Exception:                                  # any failure simply means "not available"
            return False

    def complete(self, system: str, user: str, temperature: float = 0.0, max_tokens: int = 4000) -> str:
        import requests
        payload = {"model": self.model_name, "prompt": user, "system": system, "stream": False,
                   "options": {"temperature": temperature, "num_predict": max_tokens}}
        if self.json_mode:
            payload["format"] = "json"
        try:
            r = requests.post(f"{self.host}/api/generate", json=payload, timeout=self.timeout_s)
        except requests.RequestException as e:
            raise LLMUnavailable(f"model server unreachable: {type(e).__name__}") from e
        if r.status_code != 200:
            raise LLMUnavailable(f"model server returned HTTP {r.status_code}")
        return r.json().get("response", "")


def default_gateway(cfg, cache_dir: str | Path | None = None, client=None):
    """A ready LLMGateway for the new pipeline: the local Ollama model unless another client is given.
    Disabled (deterministic path) when the config turns the LLM off or no server answers."""
    from src.llm._gateway import LLMGateway
    client = client or OllamaClient(timeout_s=cfg.budgets.llm_timeout_seconds)
    enabled = bool(cfg.llm_enabled) and (not hasattr(client, "available") or client.available())
    return LLMGateway(client=client, cache_dir=Path(cache_dir or Path(cfg.artifacts_dir) / "llm_cache"),
                      enabled=enabled, retries=cfg.thresholds.llm_retries,
                      timeout_s=cfg.budgets.llm_timeout_seconds,
                      max_output_tokens=cfg.budgets.llm_max_output_tokens,
                      stage_token_budget=cfg.budgets.stage_token_budget)
