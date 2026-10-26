# src/llm/_providers.py   (reference code; NOT executed against the live APIs in the sandbox)
"""Free-tier providers (decision E-01). Keys come ONLY from environment variables. Model IDs come from config
and must be verified against each provider's current catalog in task M8.0 (catalogs change)."""
from __future__ import annotations

import base64
import os

import httpx


class GroqClient:
    """OpenAI-compatible chat completions. Text agents. JSON mode on; reasoning effort low to save tokens."""
    base_url = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, model_name: str, timeout_s: float = 45):
        self.model_name, self.timeout_s = model_name, timeout_s
        self.api_key = os.environ["GROQ_API_KEY"]
        self.last_usage: dict | None = None

    def complete(self, system, user, temperature=0.0, max_tokens=1200, images=(), reasoning_effort=None) -> str:
        if images:
            raise ValueError("GroqClient is text-only; route vision prompts to the vision provider")
        body = {"model": self.model_name, "temperature": temperature, "max_completion_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        r = httpx.post(self.base_url, json=body, timeout=self.timeout_s,
                       headers={"Authorization": f"Bearer {self.api_key}"})
        if r.status_code == 400 and "reasoning_effort" in r.text:      # model without that parameter
            body.pop("reasoning_effort")
            r = httpx.post(self.base_url, json=body, timeout=self.timeout_s,
                           headers={"Authorization": f"Bearer {self.api_key}"})
        r.raise_for_status()
        data = r.json()
        self.last_usage = data.get("usage")
        return data["choices"][0]["message"]["content"]


class CloudflareVisionClient:
    """Workers AI OpenAI-compatible endpoint. Vision agents (pages and images as data URLs)."""

    def __init__(self, model_name: str, timeout_s: float = 90):
        self.model_name, self.timeout_s = model_name, timeout_s
        acct = os.environ["CF_ACCOUNT_ID"]
        self.url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1/chat/completions"
        self.token = os.environ["CF_API_TOKEN"]
        self.last_usage: dict | None = None

    def complete(self, system, user, temperature=0.0, max_tokens=800, images=(), reasoning_effort=None) -> str:
        content = [{"type": "text", "text": user}] + [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b).decode()}}
            for b in images]
        body = {"model": self.model_name, "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        r = httpx.post(self.url, json=body, timeout=self.timeout_s, headers={"Authorization": f"Bearer {self.token}"})
        r.raise_for_status()
        data = r.json()
        self.last_usage = data.get("usage")
        return data["choices"][0]["message"]["content"]


class CloudflareImageClient:
    """FLUX-1-schnell text-to-image. Returns JPEG bytes. Seed is logged for reproducibility."""

    def __init__(self, model_name: str = "@cf/black-forest-labs/flux-1-schnell", timeout_s: float = 120):
        acct = os.environ["CF_ACCOUNT_ID"]
        self.url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model_name}"
        self.token = os.environ["CF_API_TOKEN"]
        self.model_name, self.timeout_s = model_name, timeout_s

    def generate(self, prompt: str, seed: int, steps: int = 4) -> bytes:
        r = httpx.post(self.url, json={"prompt": prompt[:2000], "steps": steps, "seed": seed},
                       timeout=self.timeout_s, headers={"Authorization": f"Bearer {self.token}"})
        r.raise_for_status()
        return base64.b64decode(r.json()["result"]["image"])
