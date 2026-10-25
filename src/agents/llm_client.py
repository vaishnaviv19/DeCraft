# src/agents/llm_client.py  -- legacy agents API, now backed by the new pipeline's model client
"""The old agents (domain, schema, relationship, privacy) keep this interface, but every call now goes
through src.llm.OllamaClient - the same client the new pipeline's LLMGateway uses - so there is one
model connection and one set of settings (OLLAMA_HOST, DECRAFT_LLM_MODEL)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.llm import LLMUnavailable, OllamaClient


class LLMClient:
    def __init__(self, cache_dir: str = "D:/DeCraft_cache", client: OllamaClient | None = None):
        self.cache_dir = Path(cache_dir)
        self.client = client or OllamaClient(json_mode=False)   # old prompts may return arrays
        self.ollama_url, self.model = self.client.host, self.client.model_name

    def available(self) -> bool:
        return self.client.available()

    def complete(self, prompt: str, system: str = "", temperature: float = 0.1) -> Optional[str]:
        try:
            return self.client.complete(system, prompt, temperature)
        except LLMUnavailable:
            return None
