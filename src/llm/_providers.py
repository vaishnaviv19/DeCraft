# src/llm/_providers.py   (live-checked 2026-10-26: Groq text, Cloudflare vision; see scripts/smoke_llm.py)
"""Free-tier providers (decision E-01). Keys come ONLY from environment variables. Model IDs come from config
and must be verified against each provider's current catalog in task M8.0 (catalogs change)."""
from __future__ import annotations

import base64
import json
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
    """Workers AI native `ai/run` endpoint. Vision agents (pages and images as data URLs), JSON mode on.
    Measured 2026-10-26: the OpenAI-compatible endpoint rejects images for llama-3.2-11b-vision (400),
    the native one accepts the same messages; JSON mode returns an already-parsed object."""

    def __init__(self, model_name: str, timeout_s: float = 90):
        self.model_name, self.timeout_s = model_name, timeout_s
        acct = os.environ["CF_ACCOUNT_ID"]
        self.url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model_name}"
        self.token = os.environ["CF_API_TOKEN"]
        self.last_usage: dict | None = None

    def complete(self, system, user, temperature=0.0, max_tokens=800, images=(), reasoning_effort=None) -> str:
        content = [{"type": "text", "text": user}] + [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(b).decode()}}
            for b in images]
        body = {"temperature": temperature, "max_tokens": max_tokens, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        r = httpx.post(self.url, json=body, timeout=self.timeout_s, headers={"Authorization": f"Bearer {self.token}"})
        r.raise_for_status()
        result = r.json().get("result") or {}
        self.last_usage = result.get("usage")
        reply = result.get("response")
        return reply if isinstance(reply, str) else json.dumps(reply)


class CloudflareImageClient:
    """FLUX-1-schnell text-to-image. Returns JPEG bytes (~173 neurons each, measured 2026-10-26).
    The endpoint rejects a `seed` field (400 'unevaluated properties /seed'), so the seed is only kept
    for the record: the same prompt can give a different image, and reruns reuse saved images instead."""

    def __init__(self, model_name: str = "@cf/black-forest-labs/flux-1-schnell", timeout_s: float = 120):
        acct = os.environ["CF_ACCOUNT_ID"]
        self.url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model_name}"
        self.token = os.environ["CF_API_TOKEN"]
        self.model_name, self.timeout_s = model_name, timeout_s

    def generate(self, prompt: str, seed: int, width: int | None = None, height: int | None = None,
                 steps: int = 4) -> bytes:
        self.last_seed = seed                              # recorded only; the API does not accept it
        # live run 2026-10-26: the endpoint also rejects width/height (400 'unevaluated properties
        # /width, /height') and always returns a square image, so the slot's shape is cut out here
        body: dict = {"prompt": prompt[:2000], "steps": steps}
        r = httpx.post(self.url, json=body,
                       timeout=self.timeout_s, headers={"Authorization": f"Bearer {self.token}"})
        r.raise_for_status()
        image = base64.b64decode(r.json()["result"]["image"])
        if width is not None and height is not None:
            image = crop_to(image, int(width), int(height))
        return image


def crop_to(image: bytes, width: int, height: int) -> bytes:
    """Centre-crop to the width:height aspect ratio, then resize to width x height (JPEG)."""
    import io

    from PIL import Image
    with Image.open(io.BytesIO(image)) as img:
        img = img.convert("RGB")
        target = width / height
        w, h = img.size
        if w / h > target:                                 # too wide: trim the sides
            new_w = round(h * target)
            box = ((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h)
        else:                                              # too tall: trim top and bottom
            new_h = round(w / target)
            box = (0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h)
        out = io.BytesIO()
        img.crop(box).resize((width, height)).save(out, "JPEG", quality=90)
        return out.getvalue()
