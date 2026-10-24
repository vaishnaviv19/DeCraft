# src/agents/llm_client.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


class LLMClient:
    def __init__(self, cache_dir: str = "D:/DeCraft_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.model = os.environ.get("DECRAFT_LLM_MODEL", "llama3.1")

    def available(self) -> bool:
        try:
            import requests

            r = requests.get(f"{self.ollama_url}/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    def complete(self, prompt: str, system: str = "", temperature: float = 0.1) -> Optional[str]:
        try:
            import requests

            payload = {
                "model": self.model,
                "prompt": prompt,
                "system": system,
                "stream": False,
                "options": {"temperature": temperature},
            }
            r = requests.post(f"{self.ollama_url}/api/generate", json=payload, timeout=120)
            if r.status_code == 200:
                data = r.json()
                return data.get("response")
        except Exception:
            return None
        return None
