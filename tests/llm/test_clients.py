# tests/llm/test_clients.py
"""The real model client (Ollama) behind LLMGateway, shared with the legacy agents."""
import json

import requests

from src.agents.llm_client import LLMClient as LegacyClient
from src.config import load_config
from src.llm import LLMUnavailable, OllamaClient, PromptSpec, default_gateway
from pydantic import BaseModel

P = PromptSpec(name="t", version="v1", system="sys")


class Out(BaseModel):
    ok: bool


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


def test_a_reply_goes_through_the_gateway(monkeypatch, tmp_path):
    sent = {}

    def post(url, json=None, timeout=None):
        sent.update(json)
        return FakeResponse(body={"response": '{"ok": true}'})

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(OllamaClient, "available", lambda self: True)
    gateway = default_gateway(load_config(llm_enabled=True), tmp_path, client=OllamaClient(model="m"))
    assert gateway.ask_json(P, "hello", Out) == Out(ok=True)
    assert sent["format"] == "json" and sent["system"] == "sys" and sent["model"] == "m"


def test_server_down_falls_back_instead_of_crashing(monkeypatch, tmp_path):
    def post(*a, **k):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(OllamaClient, "available", lambda self: True)     # up at start, then fails
    gateway = default_gateway(load_config(llm_enabled=True), tmp_path, client=OllamaClient())
    assert gateway.ask_json(P, "hello", Out) is None                  # deterministic path takes over
    assert all(not c.ok and "unavailable" in c.error for c in gateway.log)


def test_gateway_is_disabled_when_no_model_answers(monkeypatch, tmp_path):
    monkeypatch.setattr(OllamaClient, "available", lambda self: False)
    assert default_gateway(load_config(llm_enabled=True), tmp_path).enabled is False


def test_legacy_agents_use_the_same_client_without_forcing_json(monkeypatch):
    sent = {}

    def post(url, json=None, timeout=None):
        sent.update(json)
        return FakeResponse(body={"response": "[1, 2]"})

    monkeypatch.setattr(requests, "post", post)
    legacy = LegacyClient()
    assert isinstance(legacy.client, OllamaClient)
    assert legacy.complete("prompt", system="s") == "[1, 2]" and "format" not in sent
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout()))
    assert legacy.complete("prompt") is None and issubclass(LLMUnavailable, OSError)
